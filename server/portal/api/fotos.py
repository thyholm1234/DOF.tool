"""Fotomodulet (brugerdelen): galleri, fotoside, kommentarer, upload og Mine fotos.

Alt kræver, at funktionen «fotos» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. Moderation og import ligger i
api/admin/fotos.py. SQL ligger i services (photo_*)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import activity, content_render, photo_access, photo_banners, photo_codes, photo_comments, photo_edit, photo_notify, photo_obs, photo_settings, photos

router = APIRouter(prefix="/api/fotos", tags=["fotos"])
FILTERS = {"q", "fra", "til", "md", "art", "fotograf", "land", "lok", "loknr", "sj", "koen", "alder", "dragt", "adf", "tag", "taxon"}


def need(feature: str = "fotos"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> photo_access.Access:
        acc = await photo_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


@router.get("/banner")
async def banner(acc: photo_access.Access = Depends(need("fotos_bannere")), db: AsyncSession = Depends(get_session)):
    """Aktuelt banner til galleriets top (null, hvis der intet er)."""
    return await photo_banners.current(db)


@router.get("/koder")
async def koder(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Kodelister (adfærd, køn, alder, dragt), emneord, rettigheder, sjældenhed og lande."""
    out = await photo_codes.all_codes(db)
    await db.commit()  # all_codes lægger seedet ind første gang
    return out


# ---------------------------------------------------------------- galleri og fotoside

@router.get("")
async def galleri(request: Request, side: int = Query(1, ge=1, le=100000), per: int = Query(24), sort: str = "dato", acc: photo_access.Access = Depends(need()),
                  db: AsyncSession = Depends(get_session)):
    """Galleriet: søgning og filtre (q, fra, til, md, art, fotograf, land, lok, loknr, sj, koen, alder, dragt, adf, tag). Kun publicerede fotos."""
    q = {k: v for k, v in request.query_params.items() if k in FILTERS}
    return await photos.search(db, {**q, "side": side, "per": per, "sort": sort})


@router.get("/art/{artnr}")
async def art(artnr: int, n: int = Query(10, ge=1, le=24), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artssidens galleri: artens mest likede publicerede fotos (højst ``n``) og det samlede antal fotos af arten."""
    return await photos.top_for_species(db, artnr, n)


@router.get("/n/{netfugl_id}")
async def fra_netfugl(netfugl_id: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Gamle netfugl.dk-adresser (/fotos/{id}): slå fotoet op på dets oprindelige nummer."""
    pid = await photos.by_netfugl(db, netfugl_id)
    if pid is None:
        raise HTTPException(404, "Fotoet findes ikke i arkivet")
    return {"id": pid}



# ---------------------------------------------------------------- upload, ret, slet, Mine fotos (statiske stier før /{pid})

class BirdIn(BaseModel):
    artnr: int | None = None
    taxon_id: int | None = None
    adfaerd: str | None = Field(None, max_length=16)
    koen: str | None = Field(None, max_length=16)
    alder: str | None = Field(None, max_length=16)
    dragt: str | None = Field(None, max_length=16)


class PhotoIn(BaseModel):
    dato: date
    land_id: int
    lokalitet: str = Field(max_length=250)
    loknr: int | None = None
    lat: float | None = None
    lon: float | None = None
    titel: str | None = Field(None, max_length=200)
    beskrivelse: str | None = Field(None, max_length=5000)
    fugle: list[BirdIn] = Field(min_length=1, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=4)
    kamera: dict[str, str | None] | None = None
    ryd_kamera: bool = False
    rettighed: int | None = None
    obsid: int | None = None


class SettingsIn(BaseModel):
    standard_rettighed: int
    dof_samtykke: bool


class GroupIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)


def _perr(exc: photo_edit.PhotoError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/mine")
async def mine(user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    out = await photo_edit.mine(db, user)
    out["kan_uploade"] = acc.on("fotos_upload") and out["privilegie"] != "udelukket"
    return out


@router.put("/mine/indstillinger")
async def mine_indstillinger(body: SettingsIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await photo_edit.save_settings(db, user, body.standard_rettighed, body.dof_samtykke)
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc


class PrefsIn(BaseModel):
    svar: bool | None = None
    kommentarer: bool | None = None


@router.get("/notifikationer")
async def notifikationer(user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Mine notifikationsindstillinger. «Mine fotos er godkendt/afvist» kan ikke slås fra."""
    return {**await photo_notify.get_prefs(db, user.id), "godkendelse": True}


@router.put("/notifikationer")
async def saet_notifikationer(body: PrefsIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return {**await photo_notify.set_prefs(db, user.id, svar=body.svar, kommentarer=body.kommentarer), "godkendelse": True}


@router.get("/mine-observationer")
async def mine_observationer(artnr: int, dato: date, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_dofbasen")),
                             db: AsyncSession = Depends(get_session)):
    """Brugerens egne observationer af arten ±1 dag (til «Knyt til min observation»)."""
    return await photo_obs.mine(db, user.obserkode, artnr, dato)


@router.get("/til-observationer")
async def til_observationer(obsid: str = Query("", max_length=4000), acc: photo_access.Access = Depends(need("fotos_dofbasen")), db: AsyncSession = Depends(get_session)):
    """Publicerede fotos pr. observation (højst 200 id'er): {obsid: [{id, thumb}]}."""
    ids = [int(x) for x in obsid.split(",") if x.strip().isdigit()][:200]
    return {str(k): v for k, v in (await photo_obs.for_observations(db, ids)).items()}


@router.get("/sider/retningslinjer")
async def retningslinjer(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    t = await photo_settings.get_texts(db)
    cfg = await photo_settings.get(db)
    return {"markdown": t["retningslinjer"], "html": content_render.render(t["retningslinjer"], "artikel").html if t["retningslinjer"] else "", "max_pr_art": cfg["max_pr_art"]}


@router.post("")
async def upload(file: UploadFile = File(...), data: str = Form(...), user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_upload")),
                 db: AsyncSession = Depends(get_session)):
    """Upload af et nyt foto (multipart: file + data som JSON). Privilegiet tjekkes, før filen læses."""
    try:
        await photo_edit.check_upload_allowed(db, user, acc.can_moderate)
        cfg = await photo_settings.get(db)
        raw = await file.read(cfg["upload_max_mb"] * 1024 * 1024 + 1)
        fields = PhotoIn.model_validate(photo_edit.parse_fields(data)).model_dump()
        out = await photo_edit.create(db, user, acc.can_moderate, raw, file.filename or "foto.jpg", fields)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())) from exc
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc
    await activity.track(user.id, "foto_upload", f"/fotos/{out['id']}", {"status": out["status"]})
    return out


@router.get("/{pid:int}/felter")
async def felter(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Fotoets felter til redigering (ejer eller moderator)."""
    p = await photos.get(db, pid, user_id=user.id, moderator=acc.can_moderate)
    if p is None or not (acc.can_moderate or p["egen"]):
        raise HTTPException(404, "Fotoet findes ikke")
    if p["laast"] and not acc.can_moderate:
        raise HTTPException(403, "Fotoet er låst af en moderator")
    return {"id": pid, "thumb": p["thumb"], "dato": p["dato"], "land_id": p["land"]["id"] if p["land"] else None, "lokalitet": p["lokalitet"], "loknr": p["loknr"], "lat": p["lat"], "lon": p["lon"],
            "titel": p["titel"], "beskrivelse": p["beskrivelse_md"], "tags": p["tags"], "kamera": p["kamera"], "rettighed": p["rettighed"]["id"] if p["rettighed"]["egen"] else None,
            "obsid": p["obsid"], "fugle": [{"artnr": f["artnr"], "navn": f["navn"], **f["kode"]} for f in p["fugle"]], "status": p["status"]}


@router.put("/{pid:int}")
async def ret(pid: int, body: PhotoIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        out = await photo_edit.update(db, user, acc.can_moderate, pid, body.model_dump())
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc
    await activity.track(user.id, "foto_ret", f"/fotos/{pid}")
    return out


@router.delete("/{pid:int}")
async def slet(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        foreign = await photo_edit.delete(db, user, acc.can_moderate, pid)
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc
    if foreign:
        await activity.track(user.id, "foto_slet_mod", f"/fotos/{pid}", {"foto": pid})
    return {"ok": True}


@router.post("/{pid:int}/gruppe")
async def gruppe(pid: int, body: GroupIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await photo_edit.set_group(db, user, acc.can_moderate, pid, body.ids)
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc


@router.delete("/{pid:int}/gruppe")
async def fjern_fra_gruppe(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_edit.ungroup(db, user, acc.can_moderate, pid)
    except photo_edit.PhotoError as exc:
        raise _perr(exc) from exc
    return {"ok": True}


@router.get("/{pid:int}")
async def foto(pid: int, request: Request, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    p = await photos.get(db, pid, user_id=user.id, moderator=acc.can_moderate, hide_obs=not acc.on("fotos_dofbasen"))
    if p is None:
        raise HTTPException(404, "Fotoet findes ikke")
    if p["status"] == "publiceret" and not getattr(request.state, "view_as", None):  # «se som bruger» er kun læsning: ingen visningstælling
        await photos.register_view(db, pid, user.id)
    p["kan"] = {"rette": acc.can_moderate or (p["egen"] and not p["laast"] and acc.on("fotos_upload")), "moderere": acc.can_moderate,
                "kommentere": acc.on("fotos_kommentarer") and p["status"] == "publiceret", "like": acc.on("fotos_kommentarer") and p["status"] == "publiceret",
                "bannere": acc.can_moderate and acc.on("fotos_bannere")}
    return p


@router.post("/{pid:int}/like")
async def like(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        return await photos.toggle_like(db, pid, user.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- kommentarer

class CommentIn(BaseModel):
    body: str = Field(max_length=6000)
    parent_id: int | None = None
    intern: bool = False


class EditIn(BaseModel):
    body: str = Field(max_length=6000)


class ReportIn(BaseModel):
    reason: str = Field(max_length=40)
    note: str = Field("", max_length=500)


def _cerr(exc: photo_comments.CommentError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/{pid:int}/kommentarer")
async def kommentarer(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    if await photos.get(db, pid, user_id=user.id, moderator=acc.can_moderate) is None:
        raise HTTPException(404, "Fotoet findes ikke")
    return await photo_comments.list_comments(db, pid, user.id, acc.can_moderate)


@router.post("/{pid:int}/kommentarer")
async def ny_kommentar(pid: int, body: CommentIn, tasks: BackgroundTasks, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                       db: AsyncSession = Depends(get_session)):
    try:
        c = await photo_comments.add(db, user, pid, body.body, body.parent_id, moderator=acc.can_moderate, intern=body.intern)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    if not c.intern:
        tasks.add_task(photo_notify.on_comment, c.id)
    return {"id": c.id}


@router.patch("/kommentarer/{cid}")
async def ret_kommentar(cid: int, body: EditIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                        db: AsyncSession = Depends(get_session)):
    try:
        await photo_comments.edit(db, user, cid, body.body)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True}


@router.delete("/kommentarer/{cid}")
async def slet_kommentar(cid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        await photo_comments.remove_own(db, user, cid)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True}


@router.post("/kommentarer/{cid}/anmeld")
async def anmeld(cid: int, body: ReportIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                 db: AsyncSession = Depends(get_session)):
    try:
        hidden = await photo_comments.report(db, user, cid, body.reason, body.note)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True, "skjult": hidden}
