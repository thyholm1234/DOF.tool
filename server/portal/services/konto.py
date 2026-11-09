"""DOF.tool-konti: oprettelse, login, ny adgangskode og bindingen til obserkoden.

En konto er en bruger med e-mail og egen adgangskode (scrypt, auth.hash_password). Kontoen bindes til obserkoden ved
oprettelsen: brugeren logger én gang ind med obserkode og DOFbasen-adgangskode (auth.verify_dofbasen), og først derefter
kan kontoen oprettes. Samme vej bruges til «Glemt adgangskode» – den, der kan logge ind på DOFbasen med obserkoden, ejer
kontoen. Portalen sender ingen e-mails; adressen er brugernavn og kontaktoplysning.

Forløbet:
1. verify(obserkode, DOFbasen-adgangskode) → et bindingsbevis (engangstoken, BIND_TTL), der kun gemmes som hash i Valkey.
2. complete(bevis, adgangskode, e-mail, navn) → kontoen oprettes (eller får ny adgangskode). Alle tidligere sessioner og
   positionstokens for brugeren tilbagekaldes, og api'et starter en ny session.
3. Derefter: login(e-mail eller obserkode, adgangskode) eller passkey (services/passkeys.py).

Brugere fra før kontiene (kun obserkode) har ingen pw_hash. De logges ud én gang (logout_everyone_once) og skal oprette
en konto, som bindes til deres eksisterende bruger – alle data bliver liggende.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.models import User
from portal.services import auth
from portal.services.auth import AuthError

BIND_TTL = 20 * 60  # bindingsbeviset gælder i 20 minutter
PW_MIN = 10
PW_MAX = 200
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,}$")
LOGOUT_MARK = "accounts_logout_done"
# Bruges, når kontoen ikke findes, så svaret tager lige lang tid (scrypt), og man ikke kan se forskel på tiden
_DUMMY = auth.hash_password(secrets.token_urlsafe(12))

NO_ACCOUNT = ("Obserkoden har endnu ingen DOF.tool-konto. Opret kontoen – det tager et øjeblik, og alle dine data "
              "bliver liggende.")
BAD_LOGIN = "Forkert e-mail/obserkode eller adgangskode"
COMMON = {"1234567890", "0123456789", "qwertyuiop", "adgangskode", "password123", "passw0rd12", "abcdefghij",
          "dofbasen123", "fuglekiggeri", "1q2w3e4r5t"}


def normalize_email(email: str | None) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise AuthError("Skriv en gyldig e-mailadresse", 400)
    return email


def check_new_password(password: str, obserkode: str, email: str | None) -> None:
    """Regler for en ny adgangskode: mindst PW_MIN tegn, ikke obserkoden eller e-mailen, ikke en af de mest oplagte."""
    pw = password or ""
    if len(pw) < PW_MIN:
        raise AuthError(f"Adgangskoden skal have mindst {PW_MIN} tegn", 400)
    if len(pw) > PW_MAX:
        raise AuthError(f"Adgangskoden må højst have {PW_MAX} tegn", 400)
    low = pw.lower()
    if len(set(pw)) < 4 or low in COMMON or low == obserkode.lower() or (email and low in (email, email.split("@")[0])):
        raise AuthError("Vælg en adgangskode, der er sværere at gætte", 400)


def mask_email(email: str | None) -> str | None:
    """«c•••@gmail.com» – vises, når en eksisterende konto får ny adgangskode."""
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}•••@{domain}"


def _bind_key(token: str) -> str:
    return "konto:bind:" + hashlib.sha256(token.encode()).hexdigest()


async def _by_obserkode(db: AsyncSession, kode: str) -> User | None:
    return (await db.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()


async def find_user(db: AsyncSession, ident: str) -> User | None:
    """Brugeren for en e-mailadresse eller obserkode (None, hvis den ikke findes eller er ugyldig)."""
    ident = (ident or "").strip()
    if "@" in ident:
        return (await db.execute(select(User).where(func.lower(User.email) == ident.lower()))).scalar_one_or_none()
    try:
        return await _by_obserkode(db, auth.normalize(ident))
    except AuthError:
        return None


def has_account(user: User | None) -> bool:
    """Kan brugeren logge ind? Rigtige brugere skal have oprettet en konto; testbrugere har deres lokale adgangskode."""
    return bool(user) and bool(user.pw_hash or (user.is_test and user.test_pw_hash))


# ---------------------------------------------------------------- login


async def login(db: AsyncSession, ident: str, password: str, ip: str) -> User:
    ident = (ident or "").strip()[:254]
    if not ident or not password:
        raise AuthError("Skriv e-mail eller obserkode og adgangskode", 400)
    await auth.rate_limit(ident.lower(), ip)
    user = await find_user(db, ident)
    if user is not None and not has_account(user) and not user.blocked:
        raise AuthError(NO_ACCOUNT, 409)
    stored = (user.pw_hash or (user.test_pw_hash if user.is_test else None)) if user else None
    ok = auth.check_password(stored or _DUMMY, password) and stored is not None
    if not ok:
        raise AuthError(BAD_LOGIN)
    if user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    if not user.approved:
        raise AuthError(auth.PENDING_MSG, 403)
    return user


# ---------------------------------------------------------------- oprettelse og ny adgangskode


async def verify(db: AsyncSession, obserkode: str, dof_password: str, ip: str) -> dict:
    """Trin 1: obserkoden bekræftes hos DOFbasen. Returnerer bindingsbeviset og det, formularen i trin 2 skal vise."""
    kode = auth.normalize(obserkode)
    if not dof_password:
        raise AuthError("DOFbasen-adgangskoden mangler", 400)
    await auth.rate_limit(kode, ip, prefix="bind")
    user = await _by_obserkode(db, kode)
    if user is not None and user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    if user is not None and user.is_test:
        raise AuthError("Testbrugere har ingen DOFbasen-konto. Bed en administrator om en ny adgangskode.", 400)
    await auth.verify_dofbasen(kode, dof_password)
    navn = user.navn if user is not None and user.navn not in (None, "", kode) else await auth.observer_name(db, kode)
    token = secrets.token_urlsafe(24)
    await kv().set(_bind_key(token), json.dumps({"kode": kode}), ex=BIND_TTL)
    exists = has_account(user)
    return {"token": token, "obserkode": kode, "navn": navn or "", "has_account": exists,
            "email": mask_email(user.email) if exists else None, "expires_in": BIND_TTL}


async def complete(db: AsyncSession, token: str, password: str, email: str | None = None,
                   navn: str | None = None) -> tuple[User, bool]:
    """Trin 2: opret kontoen (eller giv den en ny adgangskode). Returnerer (bruger, ny_bruger_i_portalen).

    Felterne kontrolleres, før beviset bruges, så en for svag adgangskode ikke kræver et nyt DOFbasen-login. Alle
    brugerens tidligere sessioner og positionstokens tilbagekaldes."""
    raw = await kv().get(_bind_key(token or "x"))
    if raw is None:
        raise AuthError("Bekræftelsen hos DOFbasen er udløbet. Start forfra.", 400)
    kode = json.loads(raw)["kode"]
    user = await _by_obserkode(db, kode)
    if user is not None and user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    existing = has_account(user)
    email = normalize_email(email) if (email or not existing) else None
    if email:
        clash = (await db.execute(select(User.id).where(func.lower(User.email) == email))).scalar()
        if clash is not None and (user is None or clash != user.id):
            raise AuthError("E-mailadressen bruges allerede af en anden konto", 409)
    check_new_password(password, kode, email or (user.email if user else None))
    if await kv().getdel(_bind_key(token)) is None:  # brugt i mellemtiden (to faner)
        raise AuthError("Bekræftelsen hos DOFbasen er udløbet. Start forfra.", 400)

    now = datetime.now(timezone.utc)
    new = user is None
    navn = (navn or "").strip()[:160] or None
    if new:
        user = User(obserkode=kode, navn=navn or await auth.observer_name(db, kode) or kode, role="user",
                    approved=not await auth.approval_required(db),
                    prefs={"lists_public": True, "push_enabled": True, "afdelinger": [], "kommuner": []})
        db.add(user)
    elif navn:
        user.navn = navn
    user.pw_hash = auth.hash_password(password)
    if email:
        user.email = email
    if not existing or user.account_at is None:
        user.account_at = now
    user.dof_verified_at = now
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("E-mailadressen bruges allerede af en anden konto", 409) from exc
    await db.refresh(user)
    await logout_user(user.id)
    try:  # fotografer matchet på obserkode kobles til brugeren (fotomodulet); må aldrig vælte en oprettelse
        from portal.services import photo_photographers

        await photo_photographers.claim_by_obserkode(db, user)
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
    if not user.approved:
        if new:
            await auth.notify_pending(db, user)
        raise AuthError(auth.PENDING_MSG, 403)
    return user, new


async def change_password(db: AsyncSession, user: User, current: str, new: str, ip: str) -> None:
    """Skift adgangskode som indlogget. Alle sessioner logges ud; api'et starter en ny for denne enhed."""
    await auth.rate_limit(f"pw:{user.id}", ip, prefix="pw")
    if not auth.check_password(user.pw_hash, current or ""):
        raise AuthError("Den nuværende adgangskode er forkert", 403)
    check_new_password(new, user.obserkode, user.email)
    user.pw_hash = auth.hash_password(new)
    await db.commit()
    await logout_user(user.id)


async def change_email(db: AsyncSession, user: User, current: str, email: str, ip: str) -> None:
    await auth.rate_limit(f"pw:{user.id}", ip, prefix="pw")
    if not auth.check_password(user.pw_hash, current or ""):
        raise AuthError("Adgangskoden er forkert", 403)
    email = normalize_email(email)
    clash = (await db.execute(select(User.id).where(func.lower(User.email) == email, User.id != user.id))).scalar()
    if clash is not None:
        raise AuthError("E-mailadressen bruges allerede af en anden konto", 409)
    user.email = email
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("E-mailadressen bruges allerede af en anden konto", 409) from exc


# ---------------------------------------------------------------- log ud


async def logout_user(user_id: int) -> None:
    """Log brugeren ud overalt: sessioner og telefonens positionstoken."""
    from portal.services import account, position_privacy

    await account.destroy_sessions(user_id)
    await position_privacy.revoke_token(user_id)


LOGOUT_PATTERNS = ("sess:*", "usess:*", "loctok:*", "pk:login:*", "pk:reg:*")


async def logout_everyone_once(db: AsyncSession) -> int:
    """Overgangen til DOF.tool-konti: alle, der var logget ind med obserkoden (DOFbasen-login), logges ud én gang –
    sessioner, passkey-udfordringer og telefonernes positionstokens. Markøren i settings tages atomisk, så kun én proces
    gør det, og det aldrig sker igen. Returnerer antal slettede nøgler."""
    claimed = (await db.execute(text("INSERT INTO settings (key, value) VALUES (:k, CAST('true' AS jsonb)) "
                                     "ON CONFLICT (key) DO NOTHING RETURNING key"), {"k": LOGOUT_MARK})).first()
    await db.commit()
    if claimed is None:
        return 0
    n = 0
    try:
        for pattern in LOGOUT_PATTERNS:
            async for key in kv().scan_iter(pattern):
                n += await kv().delete(key)
    except Exception:
        await db.execute(text("DELETE FROM settings WHERE key = :k"), {"k": LOGOUT_MARK})  # prøv igen ved næste start
        await db.commit()
        raise
    return n
