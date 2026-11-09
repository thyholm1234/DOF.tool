"""DOF.tool-konti: oprettelse, login, ny adgangskode og bindingen til obserkoden.

En konto er en bruger med e-mail og egen adgangskode (scrypt, auth.hash_password). Kontoen bindes til obserkoden ved
oprettelsen: brugeren logger én gang ind med obserkode og DOFbasen-adgangskode (auth.verify_dofbasen), og først derefter
kan kontoen oprettes. Samme vej bruges til «Glemt adgangskode» – den, der kan logge ind på DOFbasen med obserkoden, ejer
kontoen. E-mailadressen er brugernavn og kontaktoplysning og skal bekræftes med en kode, der sendes til den
(services/mail.py) – både ved oprettelsen og når den skiftes.

Forløbet:
1. verify(obserkode, DOFbasen-adgangskode) → et bindingsbevis (engangstoken, BIND_TTL), der kun gemmes som hash i Valkey.
2. complete(bevis, adgangskode, e-mail, navn) → bindingsbeviset byttes til en ventende oprettelse (MAIL_TTL), og en
   sekscifret kode sendes til e-mailen. Ventende oprettelser gemmes kun i Valkey (adgangskoden som scrypt-hash).
   «Glemt adgangskode» uden ny e-mail gemmes straks.
3. confirm_signup(ventende id, kode) → kontoen oprettes (eller får ny adgangskode). Alle tidligere sessioner og
   positionstokens for brugeren tilbagekaldes, og api'et starter en ny session.
4. Derefter: login(e-mail eller obserkode, adgangskode) eller passkey (services/passkeys.py).

Skift af e-mail (request_email_change → confirm_email_change) gemmer først den nye adresse, når koden, der er sendt til
den, er tastet; den gamle adresse får besked bagefter.

Brugere fra før kontiene (kun obserkode) har ingen pw_hash. De logges ud én gang (logout_everyone_once) og skal oprette
en konto, som bindes til deres eksisterende bruger – alle data bliver liggende.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.models import User
from portal.services import auth, mail
from portal.services.auth import AuthError

BIND_TTL = 20 * 60  # bindingsbeviset gælder i 20 minutter
MAIL_TTL = 30 * 60  # e-mailkoden gælder i 30 minutter
MAIL_TRIES = 5  # forkerte koder, før den ventende oprettelse/skift kasseres
MAIL_RESENDS = 3  # nye koder pr. ventende oprettelse/skift
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
                   navn: str | None = None) -> dict | tuple[User, bool]:
    """Trin 2: kontrollér felterne og byt bindingsbeviset til en ventende oprettelse, hvis en e-mailadresse skal bekræftes
    (ny konto eller ny adresse). Returnerer da {"pending": id, "email": adresse, "expires_in": …}; koden er sendt.
    «Glemt adgangskode» uden ny e-mail gemmes straks og returnerer (bruger, False).

    Felterne kontrolleres, før beviset bruges, så en for svag adgangskode ikke kræver et nyt DOFbasen-login."""
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
        await _email_free(db, email, user.id if user is not None else None)
    check_new_password(password, kode, email or (user.email if user else None))
    if email and existing and user.email and email == user.email.lower() and user.email_verified_at:
        email = None  # samme, allerede bekræftede adresse
    navn = (navn or "").strip()[:160] or None
    if await kv().getdel(_bind_key(token)) is None:  # brugt i mellemtiden (to faner)
        raise AuthError("Bekræftelsen hos DOFbasen er udløbet. Start forfra.", 400)
    pw_hash = auth.hash_password(password)
    if not email:
        return await _apply(db, kode, pw_hash, None, navn)

    pending = secrets.token_urlsafe(24)
    code = _new_code()
    rec = {"kode": kode, "email": email, "navn": navn, "pw_hash": pw_hash, "code": _code_hash(pending, code), "resends": 0}
    await kv().set(_pending_key(pending), json.dumps(rec), ex=MAIL_TTL)
    try:
        await _send_signup_code(email, pending, code, reset=existing)
    except mail.MailError as exc:
        await kv().delete(_pending_key(pending))
        raise AuthError("Vi kunne ikke sende en e-mail til adressen. Tjek den, og prøv igen.", 502) from exc
    return {"pending": pending, "email": email, "expires_in": MAIL_TTL}


async def confirm_signup(db: AsyncSession, pending: str, code: str, ip: str) -> tuple[User, bool]:
    """Trin 3: koden fra e-mailen. Opretter kontoen (eller giver den ny adgangskode og e-mail). Returnerer (bruger, ny)."""
    key = _pending_key(pending or "x")
    await auth.rate_limit(key, ip, prefix="mailcode")
    raw = await kv().get(key)
    if raw is None:
        if await kv().get(_done_key(pending or "x")):
            raise AuthError("E-mailen er allerede bekræftet, og kontoen er klar. Log ind.", 410)
        raise AuthError("Koden er udløbet. Start forfra.", 400)
    rec = json.loads(raw)
    await _check_code(key, rec["code"], _code_hash(pending, _clean_code(code)))
    if await kv().getdel(key) is None:  # brugt i mellemtiden (linket og koden på samme tid)
        raise AuthError("E-mailen er allerede bekræftet, og kontoen er klar. Log ind.", 410)
    await kv().delete(key + ":tries")
    await kv().set(_done_key(pending), "1", ex=3600)
    return await _apply(db, rec["kode"], rec["pw_hash"], rec["email"], rec["navn"])


async def resend_signup(pending: str, ip: str) -> dict:
    """Ny kode til en ventende oprettelse (den gamle holder op med at virke)."""
    key = _pending_key(pending or "x")
    await auth.rate_limit(key, ip, prefix="mailsend")
    raw = await kv().get(key)
    if raw is None:
        raise AuthError("Koden er udløbet. Start forfra.", 400)
    rec = json.loads(raw)
    if rec.get("resends", 0) >= MAIL_RESENDS:
        raise AuthError("Der er sendt for mange koder. Start forfra.", 429)
    code = _new_code()
    rec.update(code=_code_hash(pending, code), resends=rec.get("resends", 0) + 1)
    await kv().set(key, json.dumps(rec), ex=MAIL_TTL)
    await kv().delete(key + ":tries")
    try:
        await _send_signup_code(rec["email"], pending, code, reset=False)
    except mail.MailError as exc:
        raise AuthError("E-mailen kunne ikke sendes. Prøv igen om lidt.", 502) from exc
    return {"pending": pending, "email": rec["email"], "expires_in": MAIL_TTL}


async def _apply(db: AsyncSession, kode: str, pw_hash: str, email: str | None, navn: str | None) -> tuple[User, bool]:
    """Gem kontoen. email er None, når adressen ikke ændres (ny adgangskode); ellers er den netop bekræftet."""
    user = await _by_obserkode(db, kode)
    if user is not None and user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    existing = has_account(user)
    now = datetime.now(timezone.utc)
    new = user is None
    if new:
        user = User(obserkode=kode, navn=navn or await auth.observer_name(db, kode) or kode, role="user",
                    approved=not await auth.approval_required(db),
                    prefs={"lists_public": True, "push_enabled": True, "afdelinger": [], "kommuner": []})
        db.add(user)
    elif navn:
        user.navn = navn
    user.pw_hash = pw_hash
    if email:
        user.email = email
        user.email_verified_at = now
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


# ---------------------------------------------------------------- e-mailkoder


def _pending_key(pending: str) -> str:
    return "konto:mail:" + hashlib.sha256(pending.encode()).hexdigest()


def _done_key(pending: str) -> str:
    return "konto:maildone:" + hashlib.sha256(pending.encode()).hexdigest()


def _change_key(user_id: int) -> str:
    return f"konto:mailchg:{user_id}"


def _new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _clean_code(code: str | None) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())[:6]


def _code_hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


async def _check_code(key: str, stored: str, given: str) -> None:
    """Forkert kode tæller; efter MAIL_TRIES forkerte kasseres det ventende (nøglen og tælleren)."""
    if hmac.compare_digest(stored, given):
        return
    n = await kv().incr(key + ":tries")
    if n == 1:
        await kv().expire(key + ":tries", MAIL_TTL)
    if n >= MAIL_TRIES:
        await kv().delete(key, key + ":tries")
        raise AuthError("For mange forkerte koder. Start forfra.", 400)
    raise AuthError(f"Forkert kode. Du har {MAIL_TRIES - n} forsøg mere.", 400)


async def _email_free(db: AsyncSession, email: str, user_id: int | None) -> None:
    clash = (await db.execute(select(User.id).where(func.lower(User.email) == email))).scalar()
    if clash is not None and clash != user_id:
        raise AuthError("E-mailadressen bruges allerede af en anden konto", 409)


def _url(path: str) -> str:
    return get_settings().public_url.rstrip("/") + path


async def _send_signup_code(email: str, pending: str, code: str, reset: bool) -> None:
    what = "den nye e-mailadresse til din DOF.tool-konto" if reset else "din DOF.tool-konto"
    await mail.send(email, f"{code} er din kode til DOF.tool",
                    f"Hej\n\nBrug koden herunder til at bekræfte {what}:\n\n    {code}\n\n"
                    f"Eller åbn linket:\n{_url(f'/#/login?bekraeft={pending}&kode={code}')}\n\n"
                    f"Koden gælder i {MAIL_TTL // 60} minutter. Har du ikke bedt om den, kan du se bort fra denne mail.\n\n"
                    f"Venlig hilsen\n{get_settings().app_name}\n")


async def change_password(db: AsyncSession, user: User, current: str, new: str, ip: str) -> None:
    """Skift adgangskode som indlogget. Alle sessioner logges ud; api'et starter en ny for denne enhed."""
    await auth.rate_limit(f"pw:{user.id}", ip, prefix="pw")
    if not auth.check_password(user.pw_hash, current or ""):
        raise AuthError("Den nuværende adgangskode er forkert", 403)
    check_new_password(new, user.obserkode, user.email)
    user.pw_hash = auth.hash_password(new)
    await db.commit()
    await logout_user(user.id)


async def request_email_change(db: AsyncSession, user: User, current: str, email: str, ip: str) -> dict:
    """Skift (eller bekræft) e-mail, trin 1: kræver adgangskoden og sender en kode til den nye adresse. Adressen gemmes
    først i confirm_email_change. Den nuværende adresse kan sendes igen, hvis den endnu ikke er bekræftet."""
    await auth.rate_limit(f"pw:{user.id}", ip, prefix="pw")
    if not auth.check_password(user.pw_hash, current or ""):
        raise AuthError("Adgangskoden er forkert", 403)
    email = normalize_email(email)
    if user.email and email == user.email.lower() and user.email_verified_at:
        raise AuthError("Det er allerede din e-mailadresse", 400)
    await _email_free(db, email, user.id)
    key = _change_key(user.id)
    old = await kv().get(key)
    resends = json.loads(old).get("resends", 0) + 1 if old and json.loads(old)["email"] == email else 0
    if resends > MAIL_RESENDS:
        raise AuthError("Der er sendt for mange koder. Prøv igen senere.", 429)
    code = _new_code()
    await kv().set(key, json.dumps({"email": email, "code": _code_hash(key, code), "resends": resends}), ex=MAIL_TTL)
    await kv().delete(key + ":tries")
    try:
        await mail.send(email, f"{code} er din kode til DOF.tool",
                        f"Hej {user.navn or user.obserkode}\n\nBrug koden herunder til at bekræfte e-mailadressen til din "
                        f"DOF.tool-konto ({user.obserkode}):\n\n    {code}\n\n"
                        f"Eller åbn linket, mens du er logget ind:\n{_url(f'/#/profil?fane=sikkerhed&mailkode={code}')}\n\n"
                        f"Koden gælder i {MAIL_TTL // 60} minutter. Har du ikke bedt om den, kan du se bort fra denne mail.\n\n"
                        f"Venlig hilsen\n{get_settings().app_name}\n")
    except mail.MailError as exc:
        await kv().delete(key)
        raise AuthError("Vi kunne ikke sende en e-mail til adressen. Tjek den, og prøv igen.", 502) from exc
    return {"email": email, "expires_in": MAIL_TTL}


async def confirm_email_change(db: AsyncSession, user: User, code: str, ip: str) -> None:
    """Skift e-mail, trin 2: koden fra mailen. Den gamle adresse får besked (bedst muligt)."""
    key = _change_key(user.id)
    await auth.rate_limit(key, ip, prefix="mailcode")
    raw = await kv().get(key)
    if raw is None:
        raise AuthError("Koden er udløbet. Bed om en ny.", 400)
    rec = json.loads(raw)
    await _check_code(key, rec["code"], _code_hash(key, _clean_code(code)))
    await kv().delete(key, key + ":tries")
    await _email_free(db, rec["email"], user.id)
    old = user.email
    user.email = rec["email"]
    user.email_verified_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("E-mailadressen bruges allerede af en anden konto", 409) from exc
    if old and old.lower() != user.email:
        try:
            await mail.send(old, "Din e-mailadresse i DOF.tool er ændret",
                            f"Hej {user.navn or user.obserkode}\n\nE-mailadressen på din DOF.tool-konto ({user.obserkode}) er "
                            f"ændret til {mask_email(user.email)}.\n\nVar det ikke dig, så vælg en ny adgangskode via "
                            f"«Glemt adgangskode» på {_url('/#/login?konto=glemt')} – det kræver din DOFbasen-adgangskode – og "
                            f"skift e-mailen tilbage under Profil.\n\nVenlig hilsen\n{get_settings().app_name}\n")
        except mail.MailError:
            pass


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
