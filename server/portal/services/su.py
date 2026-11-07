"""SU-modulet (Sjældenhedsudvalget).

Tre formål:
* Årsrapporter: SU uploader årsrapporterne (PDF) som kladde og publicerer dem; publicerede ses af alle. Fanen står forrest.
* Åbent arkiv: godkendte SU-beskrivelser ses af alle indloggede – uden uploaderens kontaktoplysninger og uden beskederne.
* Sager: en bruger indsender en SU-beskrivelse i fire trin som på su.dof.dk (uploader, beskrivelsesinfo, dokumentation,
  bekræftelse). SU behandler sagen, og SU og indsenderen skriver frem og tilbage. SU kan også skrive interne noter.

Sagsgang (status): kladde (kun indsenderen ser den) → indsendt → behandles → afventer (SU har skrevet til indsenderen og
venter på svar; et svar sætter den tilbage til behandles) → afgjort (godkendt / ikke godkendt / henlagt).
SU-medlemmer er brugere med admin-modulet «su». Superadmin ser alt, men får ikke SU's notifikationer.
Alle tabeller ligger i brugergruppen (species er referencedata og findes i begge grupper).
"""

from __future__ import annotations

import logging
import mimetypes
import secrets
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.db import SessionLocal
from portal.models import SuCase, User
from portal.services import features, storage
from portal.services.auth import SUPERADMIN, can, ensure_roles, modules

log = logging.getLogger("portal.su")

PREFIX = "su/"  # nøgle i objektlageret: su/<sag>/<tilfældig>.<ext> (leveres kun via /api/su/files/<id> med adgangstjek)
STATUSES = {"kladde": "Kladde", "indsendt": "Indsendt", "behandles": "Under behandling", "afventer": "Afventer indsender",
            "afgjort": "Afgjort"}
OUTCOMES = {"godkendt": "Godkendt", "ikke_godkendt": "Ikke godkendt", "henlagt": "Henlagt"}
OPEN = ("indsendt", "behandles", "afventer")
# SU's regioner (som på su.dof.dk: «DK - Nordjylland» gemmes som NJ)
REGIONS = {"NJ": "DK - Nordjylland", "VJ": "DK - Vestjylland", "ØJ": "DK - Østjylland", "SJ": "DK - Sønderjylland", "FY": "DK - Fyn",
           "VS": "DK - Vestsjælland", "NS": "DK - Nordsjælland", "KBH": "DK - København", "ST": "DK - Storstrøm", "BH": "DK - Bornholm",
           "FÆ": "Færøerne", "GR": "Grønland"}
LANDE = ("Danmark", "Færøerne", "Grønland")
MAX_FILE = 50 * 1024 * 1024
MAX_REPORT = 80 * 1024 * 1024
EXT = {
    "dokument": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt"},
    "foto": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff"},
    "video": {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"},
    "lyd": {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac"},
}
EXT["andet"] = set().union(*EXT.values())  # bånd/andet: alle de kendte typer
KINDS = {"dokument": "Tekstdokument", "foto": "Foto/billeder", "video": "Video", "lyd": "Lyd", "andet": "Bånd/andet"}
INLINE = EXT["foto"] - {".heic", ".heif", ".tif", ".tiff"} | EXT["video"] - {".avi", ".mkv"} | EXT["lyd"] | {".pdf"}
for _ext, _type in ((".heic", "image/heic"), (".heif", "image/heif"), (".m4a", "audio/mp4"), (".opus", "audio/ogg"), (".oga", "audio/ogg"),
                    (".flac", "audio/flac"), (".odt", "application/vnd.oasis.opendocument.text"), (".webp", "image/webp"), (".mkv", "video/x-matroska")):
    mimetypes.add_type(_type, _ext)


class SuError(ValueError):
    pass


# ---------------------------------------------------------------- rettigheder

def is_su(user: User | None) -> bool:
    return bool(user) and (user.role == SUPERADMIN or can(user, "su"))


async def context(db: AsyncSession, user: User | None) -> dict:
    """Til /api/me og menuen: synligt, SU-medlem, ulæste sager og åbne sager (for SU)."""
    if user is None:
        return {"visible": False}
    member = is_su(user)
    visible = features.visible(await features.load(db), "su", superadmin=user.role == SUPERADMIN, insider=member)
    out = {"visible": visible, "member": member, "unread": 0, "open": 0}
    if not visible:
        return out
    out["unread"] = (await db.execute(text("""
        SELECT count(*) FROM su_cases c WHERE c.user_id = :u AND c.status <> 'kladde' AND (
            c.closed_at > coalesce(c.read_by_owner_at, 'epoch')
            OR EXISTS (SELECT 1 FROM su_messages m WHERE m.case_id = c.id AND NOT m.internal AND m.role = 'su'
                       AND m.created_at > coalesce(c.read_by_owner_at, 'epoch')))"""), {"u": user.id})).scalar() or 0
    if member:
        out["open"] = (await db.execute(text("SELECT count(*) FROM su_cases WHERE status IN ('indsendt', 'behandles', 'afventer')"))).scalar() or 0
        if user.role != SUPERADMIN:  # superadmin får ikke badges for SU's arbejde
            out["unread"] += (await db.execute(text("""
                SELECT count(*) FROM su_cases c LEFT JOIN su_reads r ON r.case_id = c.id AND r.user_id = :u
                WHERE c.status IN ('indsendt', 'behandles', 'afventer') AND c.updated_at > coalesce(r.read_at, 'epoch')"""), {"u": user.id})).scalar() or 0
    return out


async def badge_visible(db: AsyncSession, user: User | None) -> bool:
    """Vises SU's gyldne godkendt-mærke for brugeren (følger modulets synlighed)?"""
    return bool(user) and features.visible(await features.load(db), "su", superadmin=user.role == SUPERADMIN, insider=is_su(user))


def role_in(case: SuCase, user: User) -> str | None:
    """su / owner / public / None. SU vinder (et SU-medlem kan også selv indsende); kladder ses kun af indsenderen."""
    if case.status == "kladde":
        return "owner" if case.user_id == user.id else None
    if is_su(user):
        return "su"
    if case.user_id == user.id:
        return "owner"
    if case.status == "afgjort" and case.outcome == "godkendt":
        return "public"
    return None


async def su_members(db: AsyncSession) -> list[int]:
    """SU-medlemmer, der får notifikationer (ikke superadmin)."""
    await ensure_roles(db)
    rows = (await db.execute(text("SELECT id FROM users WHERE NOT blocked AND role <> :sa AND (role <> 'user' OR permissions IS NOT NULL)"),
                             {"sa": SUPERADMIN})).scalars().all()
    users = [await db.get(User, uid) for uid in rows]
    return [u.id for u in users if u and "su" in modules(u)]


# ---------------------------------------------------------------- filer

def file_kind_ok(kind: str, filename: str) -> str:
    """Filendelsen (med punktum), hvis den passer til kategorien – ellers SuError."""
    if kind not in EXT:
        raise SuError("Ukendt kategori")
    ext = ("." + filename.rsplit(".", 1)[1].lower()) if "." in filename else ""
    if ext not in EXT[kind]:
        raise SuError(f"Filtypen passer ikke til «{KINDS[kind]}». Tilladt: {', '.join(sorted(e[1:] for e in EXT[kind]))}")
    return ext


async def put_file(case_id: int, data: bytes, ext: str) -> tuple[str, str]:
    key = f"{case_id}/{secrets.token_hex(16)}{ext}"
    ctype = mimetypes.guess_type("x" + ext)[0] or "application/octet-stream"
    await storage.put(key, data, ctype, PREFIX)
    return key, ctype


def file_headers(name: str, key: str) -> dict:
    """Uploadede filer må aldrig kunne køre noget: billeder, lyd, video og PDF vises i browseren, resten hentes som download."""
    ext = "." + key.rsplit(".", 1)[1]
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._- ")[:120] or "fil"
    if ext not in INLINE:
        return {"Content-Disposition": f'attachment; filename="{safe}"', "Content-Security-Policy": "default-src 'none'; sandbox"}
    if ext == ".pdf":  # browserens PDF-visning virker ikke med sandbox
        return {"Content-Disposition": f'inline; filename="{safe}"', "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'",
                "X-Frame-Options": "SAMEORIGIN"}
    return {"Content-Disposition": f'inline; filename="{safe}"', "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; sandbox"}


async def next_sagsnr(db: AsyncSession) -> str:
    year = datetime.now(timezone.utc).year
    n = (await db.execute(text("SELECT count(*) FROM su_cases WHERE sagsnr LIKE :p"), {"p": f"{year}-%"})).scalar() or 0
    return f"{year}-{n + 1:03d}"


# ---------------------------------------------------------------- notifikationer

async def notify(case_id: int, *, to: str, title: str, body: str, exclude: int | None = None) -> None:
    """Notifikation (indbakke, live og push). to: su (SU-medlemmerne) / owner (indsenderen)."""
    from portal.services.alerts import deliver

    async with SessionLocal() as s:
        try:
            case = await s.get(SuCase, case_id)
            if case is None:
                return
            users = set(await su_members(s)) if to == "su" else ({case.user_id} if case.user_id else set())
            users.discard(exclude)
            if not users:
                return
            msg = {"title": f"SU: {title} – {case.art}"[:240], "body": body[:480], "url": f"/#/su/sag/{case.id}", "tag": f"su-{case.id}",
                   "kategori": None, "rule_id": None, "push": True}
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            await deliver(s, None, {(u, f"su:{case.id}:{stamp}"): msg for u in users})
        except Exception:  # noqa: BLE001 – en fejlet notifikation må ikke vælte handlingen
            log.exception("SU-notifikation fejlede (sag %s)", case_id)
