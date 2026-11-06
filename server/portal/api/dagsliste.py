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
from portal.services import dofbasen as svc
from portal.services import features
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
                    summering: str = Query("tur", pattern="^(tur|dato|ingen)$"), user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    d = dato or dk_today()
    if d > dk_today():
        raise HTTPException(422, "Datoen ligger i fremtiden")
    return await svc.day_observations(db, user.obserkode, d, visning, lok, lokalitet, summering)


@router.get("/obs/{obsid}")
async def observation(obsid: int, user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    """Én observation med alle DOFbasens felter (som popobs.php) og resten af turlistens indtastninger."""
    o = (await db.execute(text(f"""
        SELECT {svc.ROW_COLS}, cd.text AS dragt, o.first_seen
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN trips t ON t.turid = o.turid {svc.ROW_JOINS}
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
        WHERE o.obsid = :id AND (o.hemmelig = 0 OR o.obserkode = :me)"""), {"id": obsid, "me": user.obserkode, "sub_as_species": False})).mappings().first()
    if o is None:
        raise HTTPException(404, "Observationen findes ikke")
    o = dict(o)
    tur = []
    if o["turid"]:
        tur = [dict(r) for r in (await db.execute(text("""
            SELECT o.obsid, o.artnr, s.navn AS art, s.latin, o.antal, o.koen, o.alderkode, o.dragtkode, cd.text AS dragt,
                   o.adfkode, ca.text AS adfaerd, o.obstidfra, o.obstidtil, o.fuglnoter, o.kategori, o.loknr
            FROM observations o JOIN species s ON s.artnr = o.artnr
            LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = o.adfkode
            LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
            WHERE o.turid = :t AND (o.hemmelig = 0 OR o.obserkode = :me)
            ORDER BY s.sortering, o.obsid"""), {"t": o["turid"], "me": user.obserkode})).mappings().all()]
    return {"obs": o, "tur": tur}
