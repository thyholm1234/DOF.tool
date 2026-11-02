"""Web Push (VAPID). Nøgleparret genereres ved første opstart og gemmes i settings-tabellen."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.config import get_settings
from portal.models import PushSubscription
from portal.services import fcm
from portal.services.setting_store import get_setting, put_setting

log = logging.getLogger("portal.push")
_vapid: Vapid01 | None = None
_public_key: str | None = None
_sem = asyncio.Semaphore(16)


async def ensure_vapid(session: AsyncSession) -> str:
    global _vapid, _public_key
    if _vapid and _public_key:
        return _public_key
    stored = await get_setting(session, "vapid")
    if stored is None:
        v = Vapid01()
        v.generate_keys()
        pem = v.private_pem().decode()
        await put_setting(session, "vapid", {"private_pem": pem})
    else:
        pem = stored["private_pem"]
    _vapid = Vapid01.from_pem(pem.encode())
    raw = _vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _public_key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return _public_key


def _send_sync(sub: dict, payload: dict) -> int:
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=_vapid,
            vapid_claims={"sub": get_settings().vapid_subject},
            ttl=3600,
            headers={"Urgency": "high"},
            timeout=15,
        )
        return 201
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None) if exc.response is not None else None
        log.info("Push-fejl %s: %s", status, exc)
        return status or 500
    except Exception as exc:  # noqa: BLE001
        log.info("Push-fejl: %s", exc)
        return 500


async def send_to_user(session: AsyncSession, user_id: int, payload: dict) -> int:
    """Send til alle brugerens enheder. Udløbne abonnementer (404/410) slettes. Returnerer antal leverede."""
    return (await send_many(session, [(user_id, payload)]))[0]


async def _web(sub: PushSubscription, payload: dict) -> int:
    info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
    async with _sem:
        return await asyncio.to_thread(_send_sync, info, payload)


async def send_many(session: AsyncSession, jobs: list[tuple[int, dict]]) -> list[int]:
    """Send hver (bruger, besked) til alle brugerens enheder – alle på én gang (Web Push og app parallelt).
    Udløbne abonnementer (404/410) slettes. Returnerer antal leverede enheder pr. job, i samme rækkefølge."""
    if not jobs:
        return []
    await ensure_vapid(session)
    subs = (await session.execute(select(PushSubscription).where(
        PushSubscription.user_id.in_({u for u, _ in jobs})))).scalars().all()
    by_user: dict[int, list[PushSubscription]] = defaultdict(list)
    for s in subs:
        by_user[s.user_id].append(s)
    app, web = [], []  # (jobindeks, abonnement, besked)
    for i, (uid, payload) in enumerate(jobs):
        for s in by_user.get(uid, ()):
            (app if s.endpoint.startswith(fcm.PREFIX) else web).append((i, s, payload))
    if app:
        await fcm.ensure(session)
        if not fcm.enabled():  # Android-appen (Firebase) er ikke sat op
            app = []
    results = await asyncio.gather(
        fcm.send_many([(s.endpoint[len(fcm.PREFIX):], p) for _, s, p in app]),
        *(_web(s, p) for _, s, p in web))
    statuses = list(results[0]) + list(results[1:])
    delivered = [0] * len(jobs)
    dead, ok = set(), set()
    for (i, s, _), status in zip(app + web, statuses):
        if status in (404, 410):
            dead.add(s.id)
        elif status < 300:
            ok.add(s.id)
            delivered[i] += 1
    if dead:
        await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
    if ok - dead:
        await session.execute(update(PushSubscription).where(PushSubscription.id.in_(ok - dead))
                              .values(last_ok=datetime.now(timezone.utc)))
    return delivered
