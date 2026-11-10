"""Krydslistemodulet (brugerdelen): krydslister, seneste kryds, hvem har set hvad, blockers, årstophits, mine kryds, artslister, World Bird List og artssider.

Alt kræver, at funktionen «krydslister» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. Administration ligger i api/admin/krydslister.py.
SQL ligger i services (krydslister, kl_species, kl_admin)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import kl_access, kl_species, krydslister
from portal.services.setting_store import get_setting_dict
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/krydslister", tags=["krydslister"])


def need(feature: str = "krydslister"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> kl_access.Access:
        acc = await kl_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if await krydslister.seed(db):  # første gang: World Bird List, artslister og listetyper fra showcasens data
            await db.commit()
        return acc

    return dep


def _err(exc: krydslister.KlError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/lister")
async def lister(acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Alle krydslister (som /ranking/alle) og de parametre, der kan vælges."""
    return await krydslister.overview_lists(db)


@router.get("/oversigt")
async def oversigt(acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Forsiden: de seneste kryds på DK- og VP-listen (også 350+/700+)."""
    out: dict = {"auto_at": await krydslister.auto_at()}
    ds = await krydslister.defs(db)
    for key, min_count in (("dk", 350), ("vp", 700)):
        if key in ds:
            data = await krydslister.load_kryds(db, key)
            out[key] = krydslister.latest(data, 10)
            out[f"{key}{min_count}"] = krydslister.latest(data, 10, min_count)
    return out


@router.get("/kryds/{key}/art/{skey}")
async def kryds_art(key: str, skey: str, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Hvem har krydset arten på listen (med dato, lokalitet og evt. observationen i DOFbasen)."""
    try:
        data = await krydslister.load_kryds(db, key)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    rows = sorted(({**r, "person": data["people"].get(r["kode"], r["kode"])} for r in data["rows"] if r["key"] == skey), key=lambda r: r["dato"] or "9999")
    return {"name": data["name"], "art": rows[0]["navn"] if rows else None, "rows": rows}


@router.get("/kryds/{key}/{view}")
async def kryds_view(key: str, view: str, aar: int | None = None, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Visninger: ranking, seneste, hvem, blockers, blockerstat, tophits (aar)."""
    try:
        d, _ = await krydslister.parse_list(db, key)
        data = await krydslister.load_kryds(db, key)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    out = {"key": key, "name": data["name"], "deltagere": len(data["people"]), "auto_at": await krydslister.auto_at(), "auto": d["auto"] or bool(d["regler"].get("auto_fra"))}
    if view == "ranking":
        out["rows"] = krydslister.ranking(data)
    elif view == "seneste":
        out["rows"] = krydslister.latest(data, 100)
    elif view == "hvem":
        out.update(krydslister.who_has_what(data, await krydslister.reference(db, d["ref_list_id"], d["ref"] == "dk")))
    elif view in ("blockers", "blockerstat"):
        out.update(krydslister.blockers(data))
    elif view == "tophits":
        out["aar"] = aar or dk_today().year
        out["rows"] = krydslister.tophits(data, out["aar"])
        out["total"] = len({r["kode"] for r in data["rows"]})
    else:
        raise HTTPException(404)
    return out


# ---------------------------------------------------------------- mine kryds

class KrydsIn(BaseModel):
    liste: str = Field(max_length=40)
    taxon_id: int
    dato: date | None = None
    lokalitet: str = Field("", max_length=250)
    land: str = Field("", max_length=80)
    note: str = Field("", max_length=500)


class HideIn(BaseModel):
    liste: str = Field("dk", max_length=40)
    artnr: int
    hidden: bool = True


@router.get("/mine/{liste}")
async def mine(liste: str, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await krydslister.my_kryds(db, user, liste)
    except krydslister.KlError as exc:
        raise _err(exc) from exc


@router.post("/mine")
async def nyt_kryds(body: KrydsIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need("krydslister_manuel")), db: AsyncSession = Depends(get_session)):
    try:
        k = await krydslister.add_kryds(db, user, liste=body.liste, taxon_id=body.taxon_id, dato=body.dato, lokalitet=body.lokalitet.strip(), land=body.land.strip(),
                                        note=body.note.strip(), privileged=acc.admin)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"ok": True, "status": k.status}


@router.post("/mine/skjul")
async def skjul(body: HideIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need("krydslister_manuel")), db: AsyncSession = Depends(get_session)):
    """Skjul et automatisk kryds (fx en fejlbestemmelse i DOFbasen) – eller vis det igen."""
    try:
        await krydslister.hide_kryds(db, user, body.liste, body.artnr, body.hidden)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.delete("/mine/{kid}")
async def slet(kid: int, user: User = Depends(require_user), acc: kl_access.Access = Depends(need("krydslister_manuel")), db: AsyncSession = Depends(get_session)):
    try:
        await krydslister.delete_kryds(db, user, kid, acc.admin)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------- ædelarter og «Mine lister»

class AedelIn(BaseModel):
    artnr: int
    aar: int | None = Field(None, ge=1900, le=2100)  # None: livslisten
    aedel: bool = True
    dato: date | None = None
    lokalitet: str = Field("", max_length=250)
    obsid: int | None = None


@router.get("/aedel")
async def aedel(user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Mine ædelarter (til stjernerne på års- og livslisten)."""
    return {**await krydslister.aedel_marks(db, user), "kan_markere": acc.on("krydslister_manuel")}


@router.put("/aedel")
async def saet_aedel(body: AedelIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need("krydslister_manuel")),
                     db: AsyncSession = Depends(get_session)):
    """Markér en art fra årslisten (aar) eller livslisten som ædelart – eller fjern markeringen."""
    try:
        await krydslister.set_aedel(db, user, artnr=body.artnr, year=body.aar, on=body.aedel, dato=body.dato, lokalitet=body.lokalitet.strip(),
                                    obsid=body.obsid, privileged=acc.admin)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.get("/mine-lister")
async def mine_lister(aar: int | None = Query(None, ge=1900, le=2100), user: User = Depends(require_user), acc: kl_access.Access = Depends(need()),
                      db: AsyncSession = Depends(get_session)):
    """Mine krydslister med antal og placering (til «Mine lister»)."""
    return {"lister": await krydslister.my_overview(db, user, aar or dk_today().year), "kan_krydse": acc.on("krydslister_manuel")}


# ---------------------------------------------------------------- artslister, World Bird List og artssider

@router.get("/artslister")
async def artslister(acc: kl_access.Access = Depends(need("krydslister_wbl")), db: AsyncSession = Depends(get_session)):
    return await kl_species.species_lists(db)


@router.get("/artslister/{key}")
async def artsliste(key: str, q: str = Query("", max_length=60), acc: kl_access.Access = Depends(need("krydslister_wbl")), db: AsyncSession = Depends(get_session)):
    try:
        out = await kl_species.species_list(db, key, q)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {**out, "can_edit": acc.admin}


@router.get("/arter")
async def soeg_arter(q: str = Query("", max_length=60), liste: str | None = Query(None, max_length=24), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artssøgning til «tilføj kryds» (evt. kun arter på én artsliste)."""
    return await kl_species.search_taxa(db, q, liste)


@router.get("/wbl")
async def wbl(q: str = Query("", max_length=60), acc: kl_access.Access = Depends(need("krydslister_wbl")), db: AsyncSession = Depends(get_session)):
    return await kl_species.world_bird_list(db, q)


@router.get("/art/{taxon_id}")
async def art(taxon_id: int, acc: kl_access.Access = Depends(need("krydslister_wbl")), db: AsyncSession = Depends(get_session)):
    """Artsside: fund fra DOFbasen (pr. måned og lokalafdeling; alle fund for sjældne arter), fotos fra galleriet og kryds."""
    try:
        from portal.services import features

        flags = await features.load(db)
        photos_on = features.visible(flags, "fotos", superadmin=acc.superadmin, insider=False) or acc.superadmin
        return await kl_species.species_page(db, taxon_id, with_photos=photos_on)
    except krydslister.KlError as exc:
        raise _err(exc) from exc


@router.get("/sider/{slug}")
async def side(slug: str, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    if slug not in ("om", "regler"):
        raise HTTPException(404)
    t = await get_setting_dict(db, krydslister.TEXTS_KEY, {"om": "", "regler": ""})
    from portal.services import content_render

    return {"markdown": t[slug], "html": content_render.render(t[slug], "artikel").html if t[slug] else "", "can_edit": acc.admin}
