"""Klient til Zitadel (v2-API'et), som gemmer brugernes passkeys og TOTP-hemmeligheder og kontrollerer begge.

Zitadel kører kun i Docker-netværket uden port på værten. Kun api'et taler med den, med servicekontoens token (PAT), som
Zitadel skriver til volumen zitadel_key ved første start. Browseren og appen ser aldrig Zitadel – al login foregår gennem
api'ets egne endpoints (services/passkeys.py)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from portal.config import get_settings

log = logging.getLogger("portal.zitadel")
TIMEOUT = 10


class ZitadelError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    s = get_settings()
    return bool(s.zitadel_url) and Path(s.zitadel_pat_file).is_file()


def _token() -> str:
    return Path(get_settings().zitadel_pat_file).read_text().strip()


async def call(method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict[str, Any]:
    if not enabled():
        raise ZitadelError(503, "Passkeys og totrinslogin er ikke slået til på serveren")
    async with httpx.AsyncClient(base_url=get_settings().zitadel_url, timeout=timeout or TIMEOUT, follow_redirects=False) as client:
        try:
            r = await client.request(method, path, json=body, headers={"Authorization": f"Bearer {_token()}"})
        except httpx.HTTPError as exc:
            log.warning("Zitadel kunne ikke nås: %s", exc)
            raise ZitadelError(503, "Login-tjenesten (passkeys og totrinslogin) svarer ikke. Prøv igen om lidt.") from exc
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", "")
        except ValueError:
            msg = r.text[:200]
        log.info("Zitadel %s %s → %s %s", method, path.split("?")[0], r.status_code, msg)
        raise ZitadelError(r.status_code, msg)
    return r.json() if r.content else {}


# ---------------------------------------------------------------- brugere


async def create_user(obserkode: str, navn: str | None) -> str:
    """Opret brugeren i Zitadel (brugernavn = obserkoden, ingen adgangskode). E-mail kræves af Zitadel, men bruges ikke:
    en ikke-eksisterende adresse under .invalid, markeret som bekræftet, så der aldrig sendes post."""
    parts = (navn or obserkode).strip().split(" ", 1)
    body = {"username": obserkode,
            "profile": {"givenName": parts[0][:200] or obserkode, "familyName": (parts[1] if len(parts) > 1 else "-")[:200],
                        "displayName": (navn or obserkode)[:200]},
            "email": {"email": f"{obserkode.lower()}@brugere.doftool.invalid", "isVerified": True}}
    try:
        return (await call("POST", "/v2/users/human", body))["userId"]
    except ZitadelError as exc:
        if exc.status != 409:
            raise
    found = await find_user(obserkode)  # findes allerede (fx efter en fejl mellem oprettelse og gem i databasen)
    if not found:
        raise ZitadelError(409, "Brugeren findes i Zitadel, men kan ikke slås op")
    return found


async def find_user(obserkode: str) -> str | None:
    res = await call("POST", "/v2/users", {"queries": [{"userNameQuery": {"userName": obserkode, "method": "TEXT_QUERY_METHOD_EQUALS"}}]})
    return (res.get("result") or [{}])[0].get("userId")


async def delete_user(user_id: str) -> None:
    try:
        await call("DELETE", f"/v2/users/{user_id}")
    except ZitadelError as exc:
        if exc.status != 404:
            raise


# ---------------------------------------------------------------- passkeys


async def start_passkey(user_id: str, domain: str) -> dict:
    """→ {"passkeyId", "publicKeyCredentialCreationOptions"}"""
    return await call("POST", f"/v2/users/{user_id}/passkeys", {"domain": domain})


async def verify_passkey(user_id: str, passkey_id: str, credential: dict, name: str) -> None:
    await call("POST", f"/v2/users/{user_id}/passkeys/{passkey_id}", {"publicKeyCredential": credential, "passkeyName": name})


async def list_passkeys(user_id: str) -> list[dict]:
    res = await call("POST", f"/v2/users/{user_id}/passkeys/_search", {})
    return [p for p in res.get("result") or [] if p.get("state") == "AUTH_FACTOR_STATE_READY"]


async def remove_passkey(user_id: str, passkey_id: str) -> None:
    await call("DELETE", f"/v2/users/{user_id}/passkeys/{passkey_id}")


# ---------------------------------------------------------------- TOTP (services/totp.py)


async def auth_methods(user_id: str) -> set[str]:
    res = await call("GET", f"/v2/users/{user_id}/authentication_methods")
    return {m.removeprefix("AUTHENTICATION_METHOD_TYPE_") for m in res.get("authMethodTypes") or []}


async def has_totp(user_id: str) -> bool:
    return "TOTP" in await auth_methods(user_id)


async def start_totp(user_id: str) -> str:
    """Ny (endnu ikke aktiv) TOTP-hemmelighed; en tidligere ubekræftet erstattes. 409 = brugeren har allerede TOTP."""
    return (await call("POST", f"/v2/users/{user_id}/totp", {}))["secret"]


async def verify_totp(user_id: str, code: str) -> None:
    """Aktivér hemmeligheden med en kode fra appen (400 = forkert kode)."""
    await call("POST", f"/v2/users/{user_id}/totp/verify", {"code": code})


async def remove_totp(user_id: str) -> None:
    try:
        await call("DELETE", f"/v2/users/{user_id}/totp")
    except ZitadelError as exc:
        if exc.status != 404:
            raise


async def check_totp(user_id: str, code: str) -> dict:
    """Kontrollér en kode i en kortlivet session og returnér sessionens faktorer. Zitadel afviser en kode, der allerede
    er brugt (400), så en opsnappet kode ikke kan bruges igen."""
    s = await call("POST", "/v2/sessions", {"checks": {"user": {"userId": user_id}, "totp": {"code": code}}})
    try:
        return ((await call("GET", f"/v2/sessions/{s['sessionId']}")).get("session") or {}).get("factors") or {}
    finally:
        await end_session(s["sessionId"])


# Genoprettelseskoder: engangskoder («ab12c-3de45»), der kan bruges i stedet for TOTP-koden. Zitadel hasher dem enkeltvis,
# så det tager ~11 sekunder at lave ti og op til ~10 sekunder at afvise en forkert kode (afprøvet mod v4.20.2) – derfor
# den lange timeout.
RECOVERY_TIMEOUT = 60


async def generate_recovery_codes(user_id: str, count: int = 10) -> list[str]:
    """Nye koder (de gamle fjernes først, så kun de nye virker)."""
    await remove_recovery_codes(user_id)
    res = await call("POST", f"/v2/users/{user_id}/recovery_codes", {"count": count}, timeout=RECOVERY_TIMEOUT)
    return list(res.get("recoveryCodes") or [])


async def remove_recovery_codes(user_id: str) -> None:
    try:
        await call("DELETE", f"/v2/users/{user_id}/recovery_codes")
    except ZitadelError as exc:
        if exc.status != 404:
            raise


async def check_recovery_code(user_id: str, code: str) -> dict:
    """Som check_totp, men med en genoprettelseskode (som kun virker én gang)."""
    s = await call("POST", "/v2/sessions", {"checks": {"user": {"userId": user_id}, "recoveryCode": {"code": code}}},
                   timeout=RECOVERY_TIMEOUT)
    try:
        return ((await call("GET", f"/v2/sessions/{s['sessionId']}")).get("session") or {}).get("factors") or {}
    finally:
        await end_session(s["sessionId"])


# ---------------------------------------------------------------- sessioner (kun til at kontrollere en passkey)


async def start_session(user_id: str, domain: str) -> dict:
    """Zitadel-session for brugeren med en passkey-udfordring. → {"sessionId", "challenges": {"webAuthN": {...}}}"""
    return await call("POST", "/v2/sessions", {
        "checks": {"user": {"userId": user_id}},
        "challenges": {"webAuthN": {"domain": domain, "userVerificationRequirement": "USER_VERIFICATION_REQUIREMENT_REQUIRED"}}})


async def check_session(session_id: str, assertion: dict) -> dict:
    """Send telefonens signatur til kontrol og returnér sessionens faktorer (eller ZitadelError, hvis den ikke holder)."""
    await call("PATCH", f"/v2/sessions/{session_id}", {"checks": {"webAuthN": {"credentialAssertionData": assertion}}})
    return ((await call("GET", f"/v2/sessions/{session_id}")).get("session") or {}).get("factors") or {}


async def end_session(session_id: str) -> None:
    try:
        await call("DELETE", f"/v2/sessions/{session_id}")
    except ZitadelError:
        pass  # sessionen udløber alligevel; DOF.tool bruger den ikke efter kontrollen
