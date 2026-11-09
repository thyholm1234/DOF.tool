"""Login med passkey (Face ID, fingeraftryk, skærmlås) i stedet for DOFbasen-adgangskoden.

Første gang logger brugeren ind med obserkode og DOFbasen-adgangskode – det forbinder DOFbasen-brugeren med kontoen. Derefter
kan brugeren oprette en passkey og logge ind med den alene. Alt går gennem api'et; Zitadel (services/zitadel.py) gemmer de
offentlige nøgler og kontrollerer signaturerne, men ses aldrig af browseren eller appen.

SIKKERHED:
* Zitadel kontrollerer signatur, tæller og brugerbekræftelse, men IKKE sidens oprindelse (origin) – det er afprøvet. Derfor
  kontrollerer api'et selv clientDataJSON (type, origin og challenge), før noget sendes videre (check_client_data).
* En passkey kan kun tilføjes kort efter et login (FRESH_SECONDS), så en stjålet sessionscookie ikke kan omsættes til en
  varig adgang, der overlever «log ud overalt».
* Login med passkey kræver, at obserkoden er bekræftet hos DOFbasen inden for passkey_dof_max_days; ellers ét DOFbasen-login,
  så nedlagte DOFbasen-brugere ikke beholder adgangen for evigt.
* Udfordringerne ligger i Valkey med kort levetid og bruges kun én gang (GETDEL)."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.models import User
from portal.services import zitadel
from portal.services.auth import AuthError, normalize, session_key

LOGIN_TTL = 180
REG_TTL = 300
FRESH_SECONDS = 15 * 60
MAX_PASSKEYS = 10
TRIES_PER_MIN = 10


def origin() -> str:
    u = urlsplit(get_settings().public_url)
    return f"{u.scheme}://{u.netloc}"


def rp_id() -> str:
    return urlsplit(get_settings().public_url).hostname or "localhost"


def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def check_client_data(credential: dict, kind: str, challenge: str) -> None:
    """clientDataJSON skal være af den rigtige type, komme fra portalens egen oprindelse og svare på vores udfordring."""
    try:
        cd = json.loads(_b64u(credential["response"]["clientDataJSON"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Ugyldigt svar fra passkey", 400) from exc
    if cd.get("type") != kind or cd.get("crossOrigin") is True:
        raise AuthError("Ugyldigt svar fra passkey", 400)
    if cd.get("origin") != origin():
        raise AuthError("Passkeyen hører ikke til denne side", 400)
    if not secrets.compare_digest(str(cd.get("challenge", "")), challenge):
        raise AuthError("Udfordringen er udløbet. Prøv igen.", 400)


async def _limit(*keys: str) -> None:
    r = kv()
    for key in keys:
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, 60)
        if n > TRIES_PER_MIN:
            raise AuthError("For mange forsøg. Prøv igen om et minut.", 429)


async def _take(key: str) -> dict | None:
    raw = await kv().getdel(key)
    return json.loads(raw) if raw else None


def _zerr(exc: zitadel.ZitadelError) -> AuthError:
    return AuthError(str(exc) if exc.status == 503 else "Passkeyen kunne ikke godkendes", 503 if exc.status == 503 else 401)


# ---------------------------------------------------------------- friskt login (krav for at tilføje en passkey)


async def mark_fresh(token: str) -> None:
    await kv().set(session_key(token) + ":fresh", "1", ex=FRESH_SECONDS)


async def is_fresh(token: str | None) -> bool:
    return bool(token) and bool(await kv().exists(session_key(token) + ":fresh"))


# ---------------------------------------------------------------- login


NO_PASSKEY = "Der er ingen passkey for denne obserkode. Log ind med din DOFbasen-adgangskode, og opret en under Profil."


async def login_start(db: AsyncSession, obserkode: str, ip: str) -> dict:
    kode = normalize(obserkode)
    await _limit(f"pk:try:{kode}", f"pk:try:ip:{ip}")
    user = (await db.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if user is None or not user.zitadel_id:
        raise AuthError(NO_PASSKEY, 404)
    if user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    max_days = get_settings().passkey_dof_max_days
    if not user.is_test and (user.dof_verified_at is None
                             or datetime.now(timezone.utc) - user.dof_verified_at > timedelta(days=max_days)):
        raise AuthError(f"Log ind med din DOFbasen-adgangskode én gang – det er over {max_days} dage siden, obserkoden sidst blev "
                        "bekræftet hos DOFbasen. Derefter virker din passkey igen.", 403)
    try:
        s = await zitadel.start_session(user.zitadel_id, rp_id())
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc
    pk = s["challenges"]["webAuthN"]["publicKeyCredentialRequestOptions"]["publicKey"]
    if not pk.get("allowCredentials"):
        await zitadel.end_session(s["sessionId"])
        raise AuthError(NO_PASSKEY, 404)
    token = secrets.token_urlsafe(24)
    await kv().set(f"pk:login:{token}", json.dumps({"sid": s["sessionId"], "challenge": pk["challenge"], "uid": user.id,
                                                    "zid": user.zitadel_id}), ex=LOGIN_TTL)
    return {"token": token, "publicKey": pk}


async def login_finish(db: AsyncSession, token: str, credential: dict) -> User:
    st = await _take(f"pk:login:{token}")
    if st is None:
        raise AuthError("Login med passkey er udløbet. Prøv igen.", 400)
    try:
        check_client_data(credential, "webauthn.get", st["challenge"])
        try:
            factors = await zitadel.check_session(st["sid"], credential)
        except zitadel.ZitadelError as exc:
            raise _zerr(exc) from exc
        wa, who = factors.get("webAuthN") or {}, factors.get("user") or {}
        if not wa.get("verifiedAt") or not wa.get("userVerified") or who.get("id") != st["zid"]:
            raise AuthError("Passkeyen kunne ikke godkendes")
    finally:
        await zitadel.end_session(st["sid"])
    user = await db.get(User, st["uid"])
    if user is None or user.zitadel_id != st["zid"]:
        raise AuthError("Passkeyen kunne ikke godkendes")
    if user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    if not user.approved:
        from portal.services.auth import PENDING_MSG

        raise AuthError(PENDING_MSG, 403)
    return user


# ---------------------------------------------------------------- brugerens egne passkeys


async def ensure_zitadel_user(db: AsyncSession, user: User) -> str:
    if not user.zitadel_id:
        user.zitadel_id = await zitadel.create_user(user.obserkode, user.navn)
        await db.commit()
    return user.zitadel_id


async def list_for(user: User) -> list[dict]:
    if not user.zitadel_id or not zitadel.enabled():
        return []
    try:
        return [{"id": p["id"], "name": p.get("name") or "Passkey"} for p in await zitadel.list_passkeys(user.zitadel_id)]
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc


async def register_start(db: AsyncSession, user: User, session_token: str | None) -> dict:
    if not await is_fresh(session_token):
        raise AuthError("Log ud og ind igen for at tilføje en passkey (det skal ske inden for 15 minutter efter et login).", 403)
    try:
        zid = await ensure_zitadel_user(db, user)
        if len(await zitadel.list_passkeys(zid)) >= MAX_PASSKEYS:
            raise AuthError(f"Du kan højst have {MAX_PASSKEYS} passkeys. Slet en, før du tilføjer en ny.", 400)
        r = await zitadel.start_passkey(zid, rp_id())
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc
    pk = r["publicKeyCredentialCreationOptions"]["publicKey"]
    pk["rp"]["name"] = get_settings().app_name
    await kv().set(f"pk:reg:{user.id}", json.dumps({"pid": r["passkeyId"], "challenge": pk["challenge"]}), ex=REG_TTL)
    return {"publicKey": pk}


async def register_finish(user: User, credential: dict, name: str) -> None:
    st = await _take(f"pk:reg:{user.id}")
    if st is None or not user.zitadel_id:
        raise AuthError("Oprettelsen er udløbet. Prøv igen.", 400)
    check_client_data(credential, "webauthn.create", st["challenge"])
    try:
        await zitadel.verify_passkey(user.zitadel_id, st["pid"], credential, (name or "Passkey").strip()[:60] or "Passkey")
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc


async def remove(user: User, passkey_id: str) -> None:
    if not user.zitadel_id:
        raise AuthError("Ingen passkey", 404)
    try:
        if passkey_id not in {p["id"] for p in await zitadel.list_passkeys(user.zitadel_id)}:
            raise AuthError("Ingen passkey", 404)
        await zitadel.remove_passkey(user.zitadel_id, passkey_id)
    except zitadel.ZitadelError as exc:
        raise _zerr(exc) from exc
