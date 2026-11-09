"""Dagslisten (#/dagsliste): den traditionelle liste over dagens observationer med samme filtre som DOFbasens observationsside –
visning, dato, sortering, summering og afdeling. Slås til/fra under Admin → Funktioner (dagsliste)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import dataset, doftool_obs, features
from portal.services import dofbasen as svc
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/dagsliste", tags=["dagsliste"])


async def need_dagsliste(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "dagsliste", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("/afdelinger")
async def afdelinger(user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    return [dict(r) for r in (await db.execute(text("SELECT id, navn FROM afdelinger ORDER BY id"))).mappings()]


@router.get("")
async def dagsliste(dato: date | None = None, visning: str = Query("fugle", pattern=svc.VISNING_RE), lok: int | None = None, lokalitet: int | None = None,
                    summering: str = Query("tur", pattern="^(tur|dato|ingen)$"),
                    matrix: str | None = Query(None, pattern=r"^[0-9]+:[A-Za-z]+(\.[A-Za-z]+)*(,[0-9]+:[A-Za-z]+(\.[A-Za-z]+)*)*$", max_length=600), user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    d = dato or dk_today()
    if d > dk_today():
        raise HTTPException(422, "Datoen ligger i fremtiden")
    from portal.services import su

    return await svc.day_observations(db, user.obserkode, d, visning, lok, lokalitet, summering, su_badge=await su.badge_visible(db, user),
                                   matrix=svc.parse_matrix(matrix) if matrix else None)


@router.get("/obs/{obsid}")
async def observation(obsid: int, user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    """Én observation med alle DOFbasens felter (som popobs.php) og resten af turlistens indtastninger – fra DOFbasen
    eller DOF.tool (services/dataset.py)."""
    tool = doftool_obs.is_doftool(obsid)

    async def rows(sql: str, params: dict) -> list[dict]:
        """DOF.tool's observationer slås op i DOF.tool-gruppen (samme skema), DOFbasens i observationsgruppen."""
        if tool:
            return (await dataset.tool_fetch(sql, params))[0]
        return [dict(r) for r in (await db.execute(text(sql), params)).mappings().all()]

    found = await rows(f"""
        SELECT {svc.ROW_COLS}, cd.text AS dragt, o.first_seen
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN trips t ON t.turid = o.turid {svc.ROW_JOINS}
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
        WHERE o.obsid = :id AND (o.hemmelig = 0 OR o.obserkode = :me)""", {"id": obsid, "me": user.obserkode, "sub_as_species": False})
    o = found[0] if found else None
    if o is None:
        raise HTTPException(404, "Observationen findes ikke")
    o = dict(o)
    tur = []
    if o["turid"]:
        tur = await rows("""
            SELECT o.obsid, o.artnr, s.navn AS art, s.latin, o.antal, o.koen, o.alderkode, o.dragtkode, cd.text AS dragt,
                   o.adfkode, ca.text AS adfaerd, o.obstidfra, o.obstidtil, o.fuglnoter, o.kategori, o.loknr
            FROM observations o JOIN species s ON s.artnr = o.artnr
            LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = o.adfkode
            LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
            WHERE o.turid = :t AND (o.hemmelig = 0 OR o.obserkode = :me)
            ORDER BY s.sortering, o.obsid""", {"t": o["turid"], "me": user.obserkode})
    o["indtastning_tekst"] = svc.INDTASTNING.get(o.get("indtastning"), o.get("indtastning"))
    if tool:
        # Indtastet i DOF.tool: fotos og lyd ligger hos indtastningen, og der er intet at hente hos DOFbasen
        rid = -obsid - doftool_obs.BASE
        media = (await db.execute(text("SELECT media FROM field_reports WHERE id = :r"), {"r": rid})).scalar() or []
        o.update(report_id=rid, media={"images": [m["url"] for m in media if m.get("kind", "image") == "image"],
                                         "sounds": [m["url"] for m in media if m.get("kind") == "audio"], "dku": ""},
                 dku_status="", dku_kind=None, su_ok=False, media_pending=False)
        return {"obs": o, "tur": tur}
    # DKU-status, fotos og lyd fra DOFbasen (medie-scannerens cache); mangler den, slås observationen op i baggrunden
    from portal.dof.jobs import ensure_media_obs, media_for
    from portal.services import su

    media = await media_for(obsid)
    o["media"] = media
    o["dku_status"] = (media or {}).get("dku") or {"behandling": "Til behandling hos DKU", "dku": "Behandlet af DKU", "su": "Godkendt af SU"}.get(o.get("dku") or "", "")
    o["dku_kind"] = svc.dku_kind(o["dku_status"]) if o["dku_status"] else None
    case, su_ok = await svc.obs_marks(db, [obsid], await su.badge_visible(db, user), [svc.find_key(o["dato"], o["artnr"], o["loknr"])])
    if case:  # åben DKU-sag i portalen: lyseblåt
        o["dku_status"], o["dku_kind"] = o["dku_status"] or "DKU-sag i portalen", "behandling"
    o["su_ok"] = bool(su_ok)
    # Kun DOFbasens egne observationer (positive obsid'er) kan slås op hos DOFbasen – ikke manuelle fund
    o["media_pending"] = media is None and obsid > 0 and bool((await ensure_media_obs([obsid]))["obsids"])
    return {"obs": o, "tur": tur}
