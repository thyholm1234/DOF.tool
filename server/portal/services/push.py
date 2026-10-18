"""Web Push (VAPID). Nøgleparret genereres ved første opstart og gemmes i settings-tabellen."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.config import get_settings
from portal.models import PushSubscription, Setting

log = logging.getLogger("portal.push")
_vapid: Vapid01 | None = None
_public_key: str | None = None
_sem = asyncio.Semaphore(16)


async def ensure_vapid(session: AsyncSession) -> str:
    global _vapid, _public_key
    if _vapid and _public_key:
        return _public_key
    row = await session.get(Setting, "vapid")
    if row is None:
        v = Vapid01()
        v.generate_keys()
        pem = v.private_pem().decode()
        session.add(Setting(key="vapid", value={"private_pem": pem}))
        await session.commit()
    else:
        pem = row.value["private_pem"]
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
    await ensure_vapid(session)
    subs = (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars().all()
    delivered = 0
    for sub in subs:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        async with _sem:
            status = await asyncio.to_thread(_send_sync, info, payload)
        if status in (404, 410):
            await session.execute(delete(PushSubscription).where(PushSubscription.id == sub.id))
        elif status < 300:
            delivered += 1
            await session.execute(update(PushSubscription).where(PushSubscription.id == sub.id)
                                  .values(last_ok=datetime.now(timezone.utc)))
    return delivered
