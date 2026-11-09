"""Totrinslogin med TOTP (koder med seks cifre fra en authenticator-app: Google Authenticator, 1Password, Authy …).

Zitadel (services/zitadel.py) gemmer hemmeligheden og kontrollerer koderne, så andre DOF-apps på samme Zitadel kan bruge
den samme opsætning senere. Som med passkeys ser browseren og appen aldrig Zitadel; alt går gennem api'et.

Flow:
* Opsætning (Profil → Konto og sikkerhed): kun inden for et kvarter efter et login (som passkeys). Zitadel laver
  hemmeligheden; api'et viser den som QR-kode, og den bliver først aktiv, når brugeren har skrevet en gyldig kode.
* Login med adgangskode – og efter «Glemt adgangskode» – giver ikke en session, men et kortlivet bevis
  ({"totp": …}); sessionen kommer først med en gyldig kode (/api/auth/totp).
* Login med passkey kræver ikke koden: en passkey med brugerbekræftelse er allerede to faktorer (enhed + biometri/pin).
* Genoprettelseskoder: når TOTP slås til, laver Zitadel ti engangskoder («ab12c-3de45»), som vises én gang. Hver kan
  bruges én gang i stedet for koden fra appen (mistet telefon). Nye koder (de gamle holder op med at virke) kræver
  adgangskoden.
* Slå fra: kræver adgangskoden (koderne fjernes også). Mistet telefon uden koder: log ind med en passkey og slå det fra,
  eller bed en administrator.

SIKKERHED:
* users.totp_at spejler, at brugeren har TOTP. Svarer Zitadel ikke, afvises login for de brugere (fail closed); brugere
  uden TOTP kan stadig logge ind med adgangskode.
* Højst TRIES forkerte koder pr. bevis, og forsøg tæller også pr. bruger og IP (auth.rate_limit). Zitadel har ingen
  spærring som standard, men afviser en kode, der allerede er brugt."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.models import User
from portal.services import auth, zitadel
from portal.services.auth import AuthError

LOGIN_TTL = 5 * 60
TRIES = 5
DOWN = "Totrinslogin kan ikke kontrolleres lige nu. Prøv igen om lidt – eller log ind med en passkey."
BAD_CODE = "Forkert kode. Brug den kode, appen viser lige nu – eller en ubrugt genoprettelseskode."
RECOVERY_RE = re.compile(r"^([a-z0-9]{5})-?([a-z0-9]{5})$")


def parse(code: str | None) -> tuple[str, str]:
    """→ ("totp", seks cifre) eller ("recovery", «ab12c-3de45»)."""
    c = re.sub(r"\s", "", code or "").lower()
    if re.fullmatch(r"\d{6}", c):
        return "totp", c
    m = RECOVERY_RE.match(c)
    if m:
        return "recovery", f"{m.group(1)}-{m.group(2)}"
    raise AuthError("Skriv de seks cifre fra appen – eller en genoprettelseskode (fx ab12c-3de45)", 400)


def clean(code: str | None) -> str:
    c = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", c):
        raise AuthError("Skriv de seks cifre fra appen", 400)
    return c


def otpauth_uri(secret: str, account: str) -> str:
    """otpauth-URI'en til QR-koden. Zitadel kalder udstederen «ZITADEL»; her bruges DOF's navn."""
    issuer = get_settings().totp_issuer
    q = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": 6, "period": 30})
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?{q}"


def qr_svg(data: str) -> str:
    import segno

    return segno.make(data, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff", omitsize=True)


async def _set_flag(db: AsyncSession, user: User, on: bool) -> None:
    if bool(user.totp_at) != on:
        user.totp_at = datetime.now(timezone.utc) if on else None
        await db.commit()


# ---------------------------------------------------------------- login


async def required(db: AsyncSession, user: User) -> bool:
    """Skal brugeren skrive en kode for at logge ind med adgangskode? Zitadel er kilden (TOTP kan på sigt sættes op fra
    andre DOF-apps); flaget i users bruges, når Zitadel ikke svarer."""
    if not user.zitadel_id:
        return False
    try:
        on = await zitadel.has_totp(user.zitadel_id)
    except zitadel.ZitadelError as exc:
        if user.totp_at:
            raise AuthError(DOWN, 503) from exc
        return False
    await _set_flag(db, user, on)
    return on


async def challenge(user: User) -> dict:
    """Bevis for, at adgangskoden er godkendt. Byttes til en session med en gyldig kode."""
    token = secrets.token_urlsafe(24)
    await kv().set(f"totp:login:{token}", json.dumps({"uid": user.id, "zid": user.zitadel_id}), ex=LOGIN_TTL)
    return {"totp": token, "expires_in": LOGIN_TTL}


async def login(db: AsyncSession, token: str, code: str, ip: str) -> tuple[User, str]:
    """→ (bruger, "totp" eller "recovery")."""
    key = f"totp:login:{token}"
    raw = await kv().get(key)
    if not raw:
        raise AuthError("Login er udløbet. Log ind igen.", 400)
    st = json.loads(raw)
    await auth.rate_limit(f"totp:{st['uid']}", ip, prefix="totp")
    kind, code = parse(code)
    try:
        if kind == "recovery":
            factors = await zitadel.check_recovery_code(st["zid"], code)
        else:
            factors = await zitadel.check_totp(st["zid"], code)
    except zitadel.ZitadelError as exc:
        if exc.status == 503 or exc.status >= 500:
            raise AuthError(DOWN, 503) from exc
        n = await kv().incr(key + ":n")
        await kv().expire(key + ":n", LOGIN_TTL)
        if n >= TRIES:
            await kv().delete(key, key + ":n")
            raise AuthError("For mange forkerte koder. Log ind igen.", 401) from exc
        raise AuthError(BAD_CODE, 401) from exc
    factor = "recoveryCode" if kind == "recovery" else "totp"
    if not (factors.get(factor) or {}).get("verifiedAt") or (factors.get("user") or {}).get("id") != st["zid"]:
        raise AuthError(BAD_CODE, 401)
    if not await kv().getdel(key):  # beviset bruges kun én gang (to samtidige forsøg)
        raise AuthError("Login er udløbet. Log ind igen.", 400)
    await kv().delete(key + ":n")
    user = await db.get(User, st["uid"])
    if user is None or user.zitadel_id != st["zid"]:
        raise AuthError("Login er udløbet. Log ind igen.", 400)
    if user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    if not user.approved:
        raise AuthError(auth.PENDING_MSG, 403)
    return user, kind


# ---------------------------------------------------------------- brugerens egen opsætning


def _zerr(exc: zitadel.ZitadelError) -> AuthError:
    return AuthError(DOWN if exc.status >= 500 else str(exc) or "Zitadel afviste kaldet", 503 if exc.status >= 500 else 400)


async def status(db: AsyncSession, user: User) -> dict:
    if not zitadel.enabled():
        return {"enabled": False, "active": False}
    if not user.zitadel_id:
        return {"enabled": True, "active": False, "recovery": False}
    try:
        methods = await zitadel.auth_methods(user.zitadel_id)
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc
    await _set_flag(db, user, "TOTP" in methods)
    return {"enabled": True, "active": "TOTP" in methods, "recovery": "RECOVERY_CODE" in methods}


async def setup_start(db: AsyncSession, user: User, session_token: str | None) -> dict:
    from portal.services import passkeys

    if not await passkeys.is_fresh(session_token):
        raise AuthError("Log ud og ind igen for at slå totrinslogin til (det skal ske inden for 15 minutter efter et login).", 403)
    try:
        zid = await passkeys.ensure_zitadel_user(db, user)
        secret = await zitadel.start_totp(zid)
    except zitadel.ZitadelError as exc:
        if exc.status == 409:
            raise AuthError("Totrinslogin er allerede slået til", 409) from exc
        raise _zerr(exc) from exc
    uri = otpauth_uri(secret, user.obserkode)
    return {"secret": secret, "uri": uri, "qr": qr_svg(uri), "issuer": get_settings().totp_issuer, "account": user.obserkode}


async def setup_finish(db: AsyncSession, user: User, code: str, ip: str) -> dict:
    """Aktivér TOTP og lav genoprettelseskoderne (vises kun nu). Fejler koderne, er TOTP stadig slået til, og brugeren
    kan lave dem fra profilen."""
    await auth.rate_limit(f"totp-setup:{user.id}", ip, prefix="totp")
    code = clean(code)
    if not user.zitadel_id:
        raise AuthError("Start opsætningen igen", 400)
    try:
        await zitadel.verify_totp(user.zitadel_id, code)
    except zitadel.ZitadelError as exc:
        if exc.status < 500:
            raise AuthError("Koden passer ikke. Tjek, at telefonens ur går rigtigt, og prøv med den næste kode.", 400) from exc
        raise _zerr(exc) from exc
    await _set_flag(db, user, True)
    try:
        return {"recovery_codes": await zitadel.generate_recovery_codes(user.zitadel_id)}
    except zitadel.ZitadelError:
        return {"recovery_codes": [], "recovery_error": "Genoprettelseskoderne kunne ikke laves. Lav dem under Profil."}


async def new_recovery_codes(db: AsyncSession, user: User, password: str, ip: str) -> dict:
    """Nye genoprettelseskoder (de gamle holder op med at virke). Kræver adgangskoden og at TOTP er slået til."""
    await auth.rate_limit(f"totp-rc:{user.id}", ip, prefix="totp")
    _check_pw(user, password)
    try:
        if not user.zitadel_id or not await zitadel.has_totp(user.zitadel_id):
            raise AuthError("Slå totrinslogin til først", 400)
        return {"recovery_codes": await zitadel.generate_recovery_codes(user.zitadel_id)}
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc


def _check_pw(user: User, password: str) -> None:
    stored = user.pw_hash or (user.test_pw_hash if user.is_test else None)
    if not stored or not auth.check_password(stored, password or ""):
        raise AuthError("Forkert adgangskode", 401)


async def disable(db: AsyncSession, user: User, password: str, ip: str) -> None:
    await auth.rate_limit(f"totp-off:{user.id}", ip, prefix="totp")
    _check_pw(user, password)
    await remove(db, user)


async def remove(db: AsyncSession, user: User) -> None:
    """Fjern TOTP (brugeren selv efter adgangskoden, eller en administrator)."""
    if user.zitadel_id:
        try:
            await zitadel.remove_totp(user.zitadel_id)
            await zitadel.remove_recovery_codes(user.zitadel_id)
        except zitadel.ZitadelError as exc:
            raise _zerr(exc) from exc
    await _set_flag(db, user, False)
