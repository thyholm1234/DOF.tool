"""Firebase Cloud Messaging (HTTP v1) til Android-appen.

Opsætning: opret et Firebase-projekt, tilføj en Android-app med pakkenavnet com.chfotofilm.doftool, og hent en servicekonto-nøgle
(Projektindstillinger → Tjenestekonti → Generér ny privat nøgle). Sæt hele JSON-filen som FCM_SERVICE_ACCOUNT_JSON
(én linje) eller base64-kodet som FCM_SERVICE_ACCOUNT_B64 i .env. Enhedernes tokens gemmes i push_subscriptions som
`fcm:<token>`; udløbne tokens slettes, når FCM svarer UNREGISTERED."""

from __future__ import annotations

import base64
import json
import logging
import time
from functools import lru_cache

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from portal.config import get_settings

log = logging.getLogger("portal.fcm")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
PREFIX = "fcm:"
_token: dict = {"value": None, "exp": 0.0}
# Seneste udfald (pr. proces) – vises under Alarmer → Push & enheder, så en fejl hos Firebase kan ses
LAST: dict = {"ok_at": None, "error": None, "error_at": None}


@lru_cache
def _account() -> dict | None:
    s = get_settings()
    raw = (s.fcm_service_account_json or "").strip()
    try:
        if not raw and s.fcm_service_account_b64:
            raw = base64.b64decode(s.fcm_service_account_b64).decode()
        acc = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        log.warning("FCM: servicekontoen kunne ikke læses")
        return None
    return acc if acc and acc.get("private_key") and acc.get("client_email") and acc.get("project_id") else None


def enabled() -> bool:
    return _account() is not None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _assertion(acc: dict) -> str:
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": acc["client_email"], "scope": SCOPE, "aud": "https://oauth2.googleapis.com/token",
                              "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(acc["private_key"].encode(), password=None)
    sig = key.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{_b64(sig)}"


async def _access_token(client: httpx.AsyncClient, acc: dict) -> str:
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    r = await client.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _assertion(acc)})
    r.raise_for_status()
    body = r.json()
    _token.update(value=body["access_token"], exp=time.time() + int(body.get("expires_in", 3600)))
    return _token["value"]


def message(token: str, payload: dict) -> dict:
    tag = str(payload.get("tag") or "")
    android = {"priority": "HIGH", "ttl": "3600s", "notification": {"channel_id": "alerts", **({"tag": tag} if tag else {})}}
    return {"message": {"token": token, "notification": {"title": payload.get("title", "DOF.tool"), "body": payload.get("body", "")},
                        "data": {"url": str(payload.get("url") or "/#/"), "tag": tag}, "android": android}}


async def send(tokens: list[str], payload: dict) -> dict[str, int]:
    """Send til enhedernes tokens. Returnerer HTTP-status pr. token (200 = leveret, 404/410 = token findes ikke mere)."""
    acc = _account()
    if acc is None or not tokens:
        return {}
    out: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            bearer = {"Authorization": f"Bearer {await _access_token(client, acc)}"}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("FCM: kunne ikke hente adgangstoken: %s", exc)
            LAST.update(error=f"Adgangstoken: {exc}"[:300], error_at=time.time())
            return {t: 500 for t in tokens}
        url = f"https://fcm.googleapis.com/v1/projects/{acc['project_id']}/messages:send"
        for t in tokens:
            try:
                r = await client.post(url, json=message(t, payload), headers=bearer)
            except httpx.HTTPError as exc:
                log.info("FCM-fejl: %s", exc)
                out[t] = 500
                continue
            if r.status_code == 200:
                out[t] = 200
                LAST["ok_at"] = time.time()
            else:
                # UNREGISTERED (404) og ugyldigt token (400 INVALID_ARGUMENT) betyder, at enheden ikke kan nås mere
                dead = r.status_code == 404 or (r.status_code == 400 and "INVALID_ARGUMENT" in r.text and "token" in r.text.lower())
                log.info("FCM %s: %s", r.status_code, r.text[:200])
                LAST.update(error=f"{r.status_code}: {r.text[:260]}", error_at=time.time())
                out[t] = 410 if dead else r.status_code
    return out
