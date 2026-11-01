"""Fotos: upload, ret, slet, kvote, gruppering og «Mine fotos» (docs/netfugl-afloesning-spec.md §6, §7.4, §9.3)."""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import ContentMedia, GalBird, GalGroup, GalPhoto, GalPhotographer, User
from portal.services import content_media, content_render, imagemeta, photo_codes, photo_obs, photo_photographers, photo_privileges, photo_settings
from portal.timeutil import TZ

MAX_BIRDS = 10
TAGS = set(photo_codes.TAGS.values())
CAMERA_KEYS = ("model", "linse", "blaende", "braendvidde", "lukkertid", "iso", "flash")


class PhotoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- validering

async def _taxon(session: AsyncSession, taxon_id: int) -> dict | None:
    if (await session.execute(text("SELECT to_regclass('kl_taxa')"))).scalar() is None:
        return None
    r = (await session.execute(text("SELECT id, latin, engelsk, dansk, artnr FROM kl_taxa WHERE id = :i AND aktiv"), {"i": taxon_id})).mappings().first()
    return dict(r) if r else None


async def _clean_bird(session: AsyncSession, b: dict, codes: set[tuple[str, str]], pos: int) -> dict:
    artnr, taxon = b.get("artnr"), None
    if b.get("taxon_id"):
        taxon = await _taxon(session, b["taxon_id"])
        if taxon is None:
            raise PhotoError(400, f"Fugl {pos + 1}: arten findes ikke i World Bird List")
        artnr = artnr or taxon["artnr"]
    sp = None
    if artnr:
        sp = (await session.execute(text("SELECT artnr, navn, latin FROM species WHERE artnr = :a AND NOT excluded"), {"a": artnr})).mappings().first()
        if sp is None and taxon is None:
            raise PhotoError(400, f"Fugl {pos + 1}: ukendt art")
    if sp is None and taxon is None:
        raise PhotoError(400, f"Fugl {pos + 1}: vælg en art")
    out = {"pos": pos, "artnr": sp["artnr"] if sp else None, "taxon_id": taxon["id"] if taxon else None,
           "navn": (sp["navn"] if sp else taxon["dansk"] or taxon["engelsk"]), "latin": (sp["latin"] if sp else taxon["latin"])}
    for kind in ("adfaerd", "koen", "alder", "dragt"):
        v = b.get(kind) or None
        if v and (kind, v) not in codes:
            raise PhotoError(400, f"Fugl {pos + 1}: ukendt {kind}")
        out[kind] = v
    return out


async def clean(session: AsyncSession, d: dict) -> dict:
    """Valider og normalisér felterne fra formularen. Kaster PhotoError(400)."""
    await photo_codes.ensure_seed(session)
    codes = {(k, c) for k, c in (await session.execute(text("SELECT kind, code FROM gal_codes"))).all()}
    if not d.get("dato"):
        raise PhotoError(400, "Dato mangler")
    if isinstance(d["dato"], str):
        try:
            d["dato"] = date.fromisoformat(d["dato"])
        except ValueError as exc:
            raise PhotoError(400, "Ugyldig dato") from exc
    if d["dato"] > datetime.now(TZ).date():
        raise PhotoError(400, "Datoen ligger i fremtiden")
    if not (await session.execute(text("SELECT 1 FROM gal_countries WHERE id = :i"), {"i": d.get("land_id")})).first():
        raise PhotoError(400, "Vælg et land")
    lokalitet = (d.get("lokalitet") or "").strip()
    if not lokalitet:
        raise PhotoError(400, "Lokalitet mangler")
    lat, lon = d.get("lat"), d.get("lon")
    if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
        raise PhotoError(400, "Ugyldig koordinat")
    birds = d.get("fugle") or []
    if not 1 <= len(birds) <= MAX_BIRDS:
        raise PhotoError(400, f"Et foto skal have 1–{MAX_BIRDS} fugle")
    tags = sorted(set(d.get("tags") or []))
    if set(tags) - TAGS:
        raise PhotoError(400, "Ukendt emneord")
    if d.get("rettighed") not in (None, 1, 2, 3):
        raise PhotoError(400, "Ugyldig rettighed")
    loknr = d.get("loknr")
    if loknr and not (await session.execute(text("SELECT 1 FROM locations WHERE loknr = :l"), {"l": loknr})).first():
        loknr = None
    beskr = (d.get("beskrivelse") or "").strip()
    if len(beskr) > 5000:
        raise PhotoError(400, "Beskrivelsen er for lang (højst 5000 tegn)")
    kam = {k: str(v).strip()[:100] for k, v in (d.get("kamera") or {}).items() if k in CAMERA_KEYS and v not in (None, "")}
    return {"dato": d["dato"], "land_id": d["land_id"], "lokalitet": lokalitet[:250], "loknr": loknr, "lat": lat, "lon": lon,
            "titel": ((d.get("titel") or "").strip() or None) and d["titel"].strip()[:200], "beskrivelse": beskr or None, "tags": tags, "kamera": kam,
            "rettighed": d.get("rettighed"), "obsid": d.get("obsid"), "ryd_kamera": bool(d.get("ryd_kamera")),
            "fugle": [await _clean_bird(session, b, codes, i) for i, b in enumerate(birds)]}


async def _apply_obs(session: AsyncSession, user: User, c: dict) -> None:
    """Knyttet observation: tjek ejerskab og lad observationens felter fylde de tomme."""
    obs = await photo_obs.get_own(session, user.obserkode, c["obsid"])
    if obs is None:
        raise PhotoError(400, "Observationen findes ikke, eller den er ikke din")
    codes = await photo_obs.to_codes(session, obs)
    if not c.get("loknr") and obs["loknr"]:
        c["loknr"] = obs["loknr"]
        c["lokalitet"] = c["lokalitet"] or obs["lokalitet"]
    if c["lat"] is None and obs["lat"] is not None:
        c["lat"], c["lon"] = obs["lat"], obs["lon"]
    b0 = c["fugle"][0]
    for k in ("koen", "adfaerd", "dragt", "alder"):
        b0[k] = b0[k] or codes.get(k)


def _set_fields(photo: GalPhoto, c: dict) -> None:
    photo.dato, photo.land_id, photo.lokalitet, photo.loknr, photo.lat, photo.lon = c["dato"], c["land_id"], c["lokalitet"], c["loknr"], c["lat"], c["lon"]
    photo.titel, photo.tags, photo.rettighed, photo.obsid = c["titel"], c["tags"], c["rettighed"], c["obsid"]
    photo.beskrivelse_md = c["beskrivelse"]
    photo.beskrivelse_html = content_render.render(c["beskrivelse"], "kommentar").html if c["beskrivelse"] else None
    photo.updated_at = datetime.now(TZ)


async def _set_birds(session: AsyncSession, photo_id: int, birds: list[dict]) -> None:
    await session.execute(text("DELETE FROM gal_birds WHERE photo_id = :p"), {"p": photo_id})
    for b in birds:
        session.add(GalBird(photo_id=photo_id, **b))


# ---------------------------------------------------------------- kvote og status

def month_start(now: datetime) -> datetime:
    n = now.astimezone(TZ)
    return datetime(n.year, n.month, 1, tzinfo=TZ)


def next_month(now: datetime) -> datetime:
    n = now.astimezone(TZ)
    return datetime(n.year + (n.month == 12), n.month % 12 + 1, 1, tzinfo=TZ)


async def month_used(session: AsyncSession, user_id: int, now: datetime) -> int:
    """Fotos publiceret uden godkendelse (ingen moderator har behandlet dem) i denne kalendermåned."""
    return (await session.execute(text("""
        SELECT count(*) FROM gal_photos WHERE user_id = :u AND source = 'upload' AND status = 'publiceret' AND behandlet_af IS NULL AND published_at >= :m AND published_at < :n"""),
                                  {"u": user_id, "m": month_start(now), "n": next_month(now)})).scalar() or 0


async def decide_status(session: AsyncSession, user: User, moderator: bool, now: datetime) -> str:
    if moderator:
        return "publiceret"
    level = await photo_privileges.level(session, user.id)
    if level == "udelukket":
        raise PhotoError(403, "Du kan ikke uploade fotos")
    if level == "ubegraenset":
        return "publiceret"
    if level == "maanedlig":
        quota = (await photo_settings.get(session))["maanedlig_kvote"]
        return "publiceret" if await month_used(session, user.id, now) < quota else "afventer"
    return "afventer"


# ---------------------------------------------------------------- oprettelse, ret, slet

async def check_upload_allowed(session: AsyncSession, user: User, moderator: bool) -> None:
    """Tjek privilegiet FØR filen læses (spec §6.2.1)."""
    if not moderator and await photo_privileges.level(session, user.id) == "udelukket":
        raise PhotoError(403, "Du kan ikke uploade fotos")


async def create(session: AsyncSession, user: User, moderator: bool, data: bytes, filename: str, fields: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    cfg = await photo_settings.get(session)
    if len(data) > cfg["upload_max_mb"] * 1024 * 1024:
        raise PhotoError(413, f"Billedet er for stort (højst {cfg['upload_max_mb']} MB)")
    if content_media.sniff_image(data) is None:
        raise PhotoError(400, "Filen er ikke et billede (JPEG, PNG, GIF eller WebP)")
    c = await clean(session, dict(fields))
    if c["obsid"]:
        await _apply_obs(session, user, c)
    status = await decide_status(session, user, moderator, now)
    exif = imagemeta.read_camera(data)  # læses før strip; GPS læses ikke
    kam = {k: v for k, v in {**exif, **c["kamera"]}.items() if v}
    if c["ryd_kamera"]:
        kam = {}
    await photo_photographers.claim_by_obserkode(session, user)
    ph = await photo_photographers.find_or_create(session, user.navn or user.obserkode, user_id=user.id)
    photo = GalPhoto(user_id=user.id, photographer_id=ph.id, source="upload", status=status, uploaded_at=now, kamera=kam,
                     published_at=now if status == "publiceret" else None)
    _set_fields(photo, c)
    session.add(photo)
    await session.flush()
    try:
        m = await content_media.store_image(session, data, filename=filename or "foto.jpg", owner_kind="foto", owner_id=photo.id, user_id=user.id, credit=ph.navn[:160])
    except content_media.MediaError as exc:
        raise PhotoError(400, str(exc)) from exc
    photo.media_id, photo.width, photo.height = m.id, m.width, m.height
    await _set_birds(session, photo.id, c["fugle"])
    await session.flush()
    suggestion = await group_candidates(session, photo)
    await session.commit()
    await bump("photos")
    return {"id": photo.id, "status": status, "gruppe_forslag": suggestion}


def can_edit(photo: GalPhoto, user: User, moderator: bool) -> bool:
    return moderator or (photo.user_id == user.id and photo.locked_by is None)


async def update(session: AsyncSession, user: User, moderator: bool, pid: int, fields: dict) -> dict:
    photo = await session.get(GalPhoto, pid)
    if photo is None or (photo.user_id != user.id and not moderator):
        raise PhotoError(404, "Fotoet findes ikke")
    if not can_edit(photo, user, moderator):
        raise PhotoError(403, "Fotoet er låst af en moderator")
    c = await clean(session, dict(fields))
    if c["obsid"] and c["obsid"] != photo.obsid:
        await _apply_obs(session, user, c)
    kam = c["kamera"] if c["kamera"] or c["ryd_kamera"] else (photo.kamera or {})
    photo.kamera = {} if c["ryd_kamera"] else {k: v for k, v in kam.items() if v}
    _set_fields(photo, c)
    await _set_birds(session, pid, c["fugle"])
    if photo.status == "afvist" and photo.user_id == user.id:
        photo.status, photo.afvist_grund = "afventer", None  # rettet efter afvisning → tilbage i køen
    if photo.source == "netfugl":
        photo.locally_edited = True
    await session.commit()
    await bump("photos")
    return {"id": pid, "status": photo.status}


async def delete(session: AsyncSession, user: User, moderator: bool, pid: int) -> bool:
    """Slet fotoet og dets fil, hvis intet andet bruger den. Returnerer True, hvis det var en moderator, der slettede en andens foto (skal auditeres)."""
    photo = await session.get(GalPhoto, pid)
    if photo is None or (photo.user_id != user.id and not moderator):
        raise PhotoError(404, "Fotoet findes ikke")
    if not can_edit(photo, user, moderator):
        raise PhotoError(403, "Fotoet er låst af en moderator")
    foreign = photo.user_id != user.id
    media = await session.get(ContentMedia, photo.media_id) if photo.media_id else None
    gid = photo.group_id
    await session.delete(photo)
    await session.flush()
    if media is not None:
        await content_media.delete_media(session, media)
    if gid:
        await _repair_group(session, gid)
    await session.commit()
    await bump("photos")
    return foreign


# ---------------------------------------------------------------- grupper

async def _repair_group(session: AsyncSession, gid: int) -> None:
    members = [r[0] for r in (await session.execute(text("SELECT id FROM gal_photos WHERE group_id = :g ORDER BY id"), {"g": gid})).all()]
    g = await session.get(GalGroup, gid)
    if g is None:
        return
    if len(members) < 2:
        await session.execute(text("UPDATE gal_photos SET group_id = NULL WHERE group_id = :g"), {"g": gid})
        await session.delete(g)
    elif g.cover_photo_id not in members:
        g.cover_photo_id = members[0]


async def group_candidates(session: AsyncSession, photo: GalPhoto) -> list[int]:
    """Andre fotos fra samme fotograf af samme fund (samme observation, eller samme hovedart + dato + lokalitet)."""
    if photo.photographer_id is None:
        return []
    art = (await session.execute(select(GalBird.artnr).where(GalBird.photo_id == photo.id, GalBird.pos == 0))).scalar_one_or_none()
    rows = (await session.execute(text("""
        SELECT p.id FROM gal_photos p WHERE p.photographer_id = :ph AND p.id <> :id AND p.group_id IS NULL AND p.status <> 'afpubliceret'
          AND ((CAST(:obs AS bigint) IS NOT NULL AND p.obsid = :obs)
               OR (CAST(:art AS int) IS NOT NULL AND p.dato = :dato AND p.dato IS NOT NULL
                   AND (p.loknr IS NOT DISTINCT FROM :lok AND (CAST(:lok AS int) IS NOT NULL OR p.lokalitet = :lokalitet))
                   AND EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.pos = 0 AND b.artnr = :art)))
        ORDER BY p.id LIMIT 20"""), {"ph": photo.photographer_id, "id": photo.id, "obs": photo.obsid, "art": art, "dato": photo.dato, "lok": photo.loknr,
                                     "lokalitet": photo.lokalitet})).all()
    return [r[0] for r in rows]


async def set_group(session: AsyncSession, user: User, moderator: bool, pid: int, ids: list[int]) -> dict:
    """Saml pid og ids i én gruppe. Ejeren kan kun gruppere egne fotos; moderatorer alle."""
    photos = list((await session.execute(select(GalPhoto).where(GalPhoto.id.in_([pid, *ids])))).scalars())
    if len(photos) != len(set([pid, *ids])):
        raise PhotoError(404, "Et af fotoene findes ikke")
    if not moderator and any(p.user_id != user.id for p in photos):
        raise PhotoError(403, "Du kan kun gruppere dine egne fotos")
    if len(photos) < 2:
        raise PhotoError(400, "Vælg mindst ét foto mere")
    gid = next((p.group_id for p in photos if p.group_id), None)
    first = min(photos, key=lambda p: p.id)
    if gid is None:
        art = (await session.execute(select(GalBird.artnr).where(GalBird.photo_id == first.id, GalBird.pos == 0))).scalar_one_or_none()
        g = GalGroup(photographer_id=first.photographer_id, artnr=art, dato=first.dato, cover_photo_id=first.id)
        session.add(g)
        await session.flush()
        gid = g.id
    old = {p.group_id for p in photos if p.group_id and p.group_id != gid}
    for p in photos:
        p.group_id = gid
    await session.flush()
    for o in old:
        await _repair_group(session, o)
    await session.commit()
    await bump("photos")
    return {"group_id": gid}


async def ungroup(session: AsyncSession, user: User, moderator: bool, pid: int) -> None:
    photo = await session.get(GalPhoto, pid)
    if photo is None or (photo.user_id != user.id and not moderator):
        raise PhotoError(404, "Fotoet findes ikke")
    gid = photo.group_id
    photo.group_id = None
    await session.flush()
    if gid:
        await _repair_group(session, gid)
    await session.commit()
    await bump("photos")


# ---------------------------------------------------------------- Mine fotos

async def mine(session: AsyncSession, user: User, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    rows = (await session.execute(text("""
        SELECT p.id, p.titel, p.dato, p.lokalitet, p.status, p.afvist_grund, p.locked_by IS NOT NULL AS laast, p.likes + p.likes_imported AS likes,
               p.views + p.views_imported AS views, p.comments, m.thumb_key, m.storage_key,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('navn', b.navn, 'artnr', b.artnr) ORDER BY b.pos), '[]'::jsonb) FROM gal_birds b WHERE b.photo_id = p.id) AS birds
        FROM gal_photos p LEFT JOIN content_media m ON m.id = p.media_id WHERE p.user_id = :u ORDER BY p.uploaded_at DESC, p.id DESC LIMIT 1000"""), {"u": user.id})).mappings().all()
    ph = (await session.execute(select(GalPhotographer).where(GalPhotographer.user_id == user.id))).scalar_one_or_none()
    cfg = await photo_settings.get(session)
    level = await photo_privileges.level(session, user.id)
    return {"fotos": [{"id": r["id"], "titel": r["titel"], "dato": r["dato"].isoformat() if r["dato"] else None, "lokalitet": r["lokalitet"], "status": r["status"],
                       "afvist_grund": r["afvist_grund"], "laast": r["laast"], "likes": r["likes"], "views": r["views"], "kommentarer": r["comments"],
                       "thumb": "/media/art/" + (r["thumb_key"] or r["storage_key"]) if (r["thumb_key"] or r["storage_key"]) else None, "arter": r["birds"]} for r in rows],
            "indstillinger": {"standard_rettighed": ph.standard_rettighed if ph else 2, "dof_samtykke": bool(ph and ph.dof_samtykke)},
            "privilegie": level, "privilegie_tekst": photo_privileges.TEXTS[level],
            "kvote": {"brugt": await month_used(session, user.id, now), "i_alt": cfg["maanedlig_kvote"]} if level == "maanedlig" else None}


async def save_settings(session: AsyncSession, user: User, standard_rettighed: int, dof_samtykke: bool) -> dict:
    if standard_rettighed not in (1, 2, 3):
        raise PhotoError(400, "Ugyldig rettighed")
    await photo_photographers.claim_by_obserkode(session, user)
    ph = await photo_photographers.find_or_create(session, user.navn or user.obserkode, user_id=user.id)
    ph.standard_rettighed, ph.dof_samtykke = standard_rettighed, bool(dof_samtykke)
    await session.commit()
    await bump("photos")
    return {"standard_rettighed": ph.standard_rettighed, "dof_samtykke": ph.dof_samtykke}


def parse_fields(raw: str) -> dict:
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise PhotoError(400, "Ugyldige felter") from exc
    if not isinstance(d, dict):
        raise PhotoError(400, "Ugyldige felter")
    return d
