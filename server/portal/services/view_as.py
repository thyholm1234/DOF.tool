"""Se som bruger (docs/netfugl-afloesning-spec.md §13): superadmin ser appen som en anden bruger i 15 minutter – kun læsning, logget.

Tilstanden ligger i Valkey pr. superadmin-session (``viewas:<token>``) og udløber af sig selv. api/deps.optional_user returnerer målbrugeren, mens tilstanden
er gyldig; middleware i main.py afviser alle ændrende kald (og SSE) i tilstanden, så intet endpoint kan glemme det."""

from __future__ import annotations

import json
import time

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.models import User
from portal.services import activity
from portal.services.auth import SUPERADMIN, token_hash

TTL = 15 * 60
KEY = "viewas:"
# Kald der stadig er tilladt i tilstanden: at afslutte den (og at logge ud)
ALLOWED = (("DELETE", "/api/admin/se-som"), ("POST", "/api/auth/logout"))


def key_for(token: str) -> str:
    """Tilstanden gemmes under hashen af sessionscookien – aldrig selve cookien."""
    return KEY + token_hash(token)


class ViewAsError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def current(token: str | None) -> dict | None:
    if not token:
        return None
    raw = await kv().get(key_for(token))
    if not raw:
        return None
    d = json.loads(raw)
    return d if d["until"] > time.time() else None


async def start(session: AsyncSession, real: User, token: str, user_id: int, grund: str) -> dict:
    if real.role != SUPERADMIN:
        raise ViewAsError(403, "Kun superadmin kan se appen som en anden bruger")
    grund = (grund or "").strip()
    if len(grund) < 3:
        raise ViewAsError(400, "Angiv en grund (fx supportsag)")
    target = await session.get(User, user_id)
    if target is None or target.blocked:
        raise ViewAsError(404, "Brugeren findes ikke, eller er spærret")
    if target.id == real.id:
        raise ViewAsError(400, "Du ser allerede appen som dig selv")
    until = time.time() + TTL
    d = {"user_id": target.id, "until": until, "by": real.id, "grund": grund[:300]}
    await kv().set(key_for(token), json.dumps(d), ex=TTL)
    await activity.track(real.id, "se_som", None, {"bruger": target.id, "obserkode": target.obserkode, "grund": grund[:300]})
    return d


async def end(real: User | None, token: str | None) -> None:
    d = await current(token)
    if token:
        await kv().delete(key_for(token))
    if d and real is not None:
        await activity.track(real.id, "se_som_slut", None, {"bruger": d["user_id"]})


def blocked(method: str, path: str) -> bool:
    """Skal kaldet afvises i tilstanden? Alt andet end læsning (og de to undtagelser) – samt SSE-strømmen."""
    if path.startswith("/api/stream"):
        return True
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    return (method, path.rstrip("/")) not in ALLOWED
