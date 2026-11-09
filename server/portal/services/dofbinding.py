"""Forbindelsen mellem en DOF.tool-konto og en DOFbasen-obserkode.

En konto kan være uden forbindelse til DOFbasen: oprettet uden (konto.request_local_signup), eller fordi brugeren har
fjernet den (unbind). Den har da en lokal kode, DT + kontoens id (DT000042), i users.obserkode – kolonnen er stadig
nøglen for indtastninger, kryds og listegrupper. Rigtige obserkoder starter med et ciffer eller et par kendte
bogstavpræfikser (DOF…, OSS…, IC…); ingen starter med DT, og koden kontrolleres alligevel mod observatørregistret.

* unbind: kræver adgangskoden. Det, brugeren har lavet i DOF.tool under obserkoden (egne indtastninger, manuelle kryds,
  medlemskab af listegrupper), flyttes til den lokale kode; DOFbasens egne observationer bliver ved obserkoden, som nu er
  ledig – ejeren kan oprette en ny konto med den. Superadmin kan kun fjerne forbindelsen, når rollen er bundet til
  e-mailen (SUPERADMIN_EMAIL) og ikke til obserkoden.
* bind: med ét DOFbasen-login, og kun hvis ingen anden konto har obserkoden. Det under den lokale kode flyttes med.
* Zitadel-brugernavnet følger koden (zitadel.rename_user), så en ny konto på en frigivet obserkode aldrig kan blive
  koblet til den gamle kontos passkeys og totrinslogin (users.zitadel_id er desuden unik)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, worker_command
from portal.models import User
from portal.services import auth, zitadel
from portal.services.auth import AuthError

log = logging.getLogger("portal.dofbinding")
LOCAL_RE = re.compile(r"^DT\d{6,}$")

# Det, brugeren selv har lavet i DOF.tool under koden (alle i brugergruppen). Indtastninger på vegne af en anden (fra
# før den funktion blev fjernet) har en anden obserkode og bliver liggende.
MOVE_SQL = (
    "UPDATE field_trips SET obserkode = :new WHERE user_id = :uid AND obserkode = :old",
    "UPDATE field_reports SET obserkode = :new WHERE user_id = :uid AND obserkode = :old",
    "UPDATE kl_kryds SET obserkode = :new WHERE obserkode = :old",
    "UPDATE nf_kryds SET obserkode = :new WHERE obserkode = :old",
    "DELETE FROM list_group_members m WHERE m.obserkode = :new AND EXISTS "
    "(SELECT 1 FROM list_group_members o WHERE o.group_id = m.group_id AND o.obserkode = :old)",
    "UPDATE list_group_members SET obserkode = :new WHERE obserkode = :old",
)


def local_code(user_id: int) -> str:
    return f"DT{user_id:06d}"


def is_local(kode: str | None) -> bool:
    return bool(LOCAL_RE.fullmatch(kode or ""))


def is_bound(user: User) -> bool:
    """Er kontoen bundet til en rigtig DOFbasen-obserkode? (Testbrugere er ikke.)"""
    return not getattr(user, "is_test", False) and not is_local(user.obserkode)


def _check_pw(user: User, password: str) -> None:
    if not user.pw_hash or not auth.check_password(user.pw_hash, password or ""):
        raise AuthError("Forkert adgangskode", 401)


async def _code_taken(db: AsyncSession, kode: str, user_id: int) -> bool:
    other = (await db.execute(select(User.id).where(User.obserkode == kode, User.id != user_id))).first()
    known = (await db.execute(text("SELECT 1 FROM observers WHERE obserkode = :k"), {"k": kode})).first()
    return other is not None or known is not None


async def _move(db: AsyncSession, user: User, new: str) -> str:
    """Flyt brugerens egne data og selve koden fra den nuværende til new. Zitadel omdøbes først (og tilbage ved fejl)."""
    old = user.obserkode
    if user.zitadel_id:
        try:
            await zitadel.rename_user(user.zitadel_id, new)
        except zitadel.ZitadelError as exc:
            raise AuthError("Login-tjenesten svarer ikke. Prøv igen om lidt.", 503) from exc
    try:
        for sql in MOVE_SQL:
            await db.execute(text(sql), {"new": new, "old": old, "uid": user.id})
        user.obserkode = new
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if user.zitadel_id:
            try:
                await zitadel.rename_user(user.zitadel_id, old)
            except zitadel.ZitadelError:
                log.error("Zitadel-brugeren %s hedder nu %s, men kontoen har stadig %s – ret det manuelt", user.zitadel_id, new, old)
        if isinstance(exc, IntegrityError):
            raise AuthError("Obserkoden har allerede en konto i DOF.tool", 409) from exc
        raise
    await worker_command("doftool_sync")  # DOF.tool-datasættet bygges om med den nye kode
    await bump("lists")
    return old


async def unbind(db: AsyncSession, user: User, password: str, ip: str) -> str:
    """Fjern forbindelsen til DOFbasen. Returnerer den obserkode, kontoen var bundet til."""
    await auth.rate_limit(f"unbind:{user.id}", ip, prefix="bind")
    if not is_bound(user):
        raise AuthError("Kontoen er ikke bundet til en obserkode", 400)
    if user.role == auth.SUPERADMIN and not auth.superadmin_by_email():
        raise AuthError("Superadmin er obserkoden fra .env (SUPERADMIN_OBSERKODE) og kan ikke fjerne forbindelsen. "
                        "Bind superadmin til e-mailen med SUPERADMIN_EMAIL først.", 400)
    _check_pw(user, password)
    new = local_code(user.id)
    if await _code_taken(db, new, user.id):
        raise AuthError("Den lokale kode er optaget – kontakt en administrator", 409)
    old = await _move(db, user, new)
    user.dof_verified_at = None
    await db.commit()
    return old


async def bind(db: AsyncSession, user: User, obserkode: str, dof_password: str, ip: str) -> None:
    """Bind en konto uden forbindelse til en obserkode med ét DOFbasen-login – kun hvis ingen anden konto har koden."""
    if is_bound(user) or user.is_test:
        raise AuthError("Kontoen er allerede bundet til en obserkode", 400)
    kode = auth.normalize(obserkode)
    if is_local(kode):
        raise AuthError("Ugyldig obserkode", 400)
    if not dof_password:
        raise AuthError("DOFbasen-adgangskoden mangler", 400)
    await auth.rate_limit(kode, ip, prefix="bind")
    if (await db.execute(select(User.id).where(User.obserkode == kode))).first() is not None:
        raise AuthError("Obserkoden har allerede en konto i DOF.tool. Log ind på den i stedet – eller bed en administrator om hjælp.", 409)
    await auth.verify_dofbasen(kode, dof_password)
    await _move(db, user, kode)
    user.dof_verified_at = datetime.now(timezone.utc)
    user.history_synced_at = None
    if not user.navn or is_local(user.navn):
        user.navn = await auth.observer_name(db, kode) or user.navn
    await db.commit()
    try:  # fotografer matchet på obserkoden kobles til brugeren (fotomodulet)
        from portal.services import photo_photographers

        await photo_photographers.claim_by_obserkode(db, user)
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
