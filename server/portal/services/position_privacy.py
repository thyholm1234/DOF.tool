"""Værn om brugernes position (baggrundspositionen fra appen og 'Nær mig').

Positionen er den mest følsomme oplysning i portalen: den viser, hvor en person er – også når appen er lukket. Reglerne:

* Positionen gemmes afrundet (ca. 100 m) – mere præcision skal 'Nær mig' ikke bruge.
* Positionstokenet ligger kun som SHA-256 i Valkey, så en kopi af Valkey (backup, dump) ikke kan bruges til at melde
  positioner i en brugers navn.
* Ingen andre end brugeren selv ser positionen – heller ikke en superadmin i «se som bruger». Det gælder også alt, der
  kan afsløre den: 'Nær mig'-områdets geometri, feed og lister for området og notifikationer fra regler på området.
* Koordinater i adresser (scope=near:…) skrives ikke i access-loggen.
* Positionstokenet er bundet til den session, der hentede det: logger man ud på telefonen, holder den op med at kunne
  melde positioner (indtil man logger ind igen).
* Slår man delingen fra, glemmes også Nær mig-områdets cirkel – ellers ville den blive stående om den sidste position."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv

ME = "me"
DECIMALS = 3  # ca. 111 m nord-syd, 65 m øst-vest i Danmark
TOKEN_TTL = 180 * 86400  # positionstokenet fornyes, hver gang appen starter delingen
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")  # secrets.token_urlsafe(32) giver 43 tegn


def coarse(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, DECIMALS), round(lon, DECIMALS)


def token_key(token: str) -> str:
    """Valkey-nøglen for et positionstoken: kun hashen gemmes."""
    return "loctok:h:" + hashlib.sha256(token.encode()).hexdigest()


def _session_key(session_token: str) -> str:
    return "loctok:sess:" + hashlib.sha256(session_token.encode()).hexdigest()


async def revoke_token(user_id: int) -> None:
    """Gør brugerens positionstoken ugyldigt (og glem hvilken session, der hentede det)."""
    old = await kv().get(f"loctok:user:{user_id}")
    sess = await kv().get(f"loctok:user:{user_id}:sess")
    keys = [f"loctok:user:{user_id}", f"loctok:user:{user_id}:sess"] + ([sess] if sess else [])
    if old:
        # loctok:user:<id> peger på hashens nøgle; ældre versioner gemte selve tokenet (loctok:<token>)
        keys.append(old if old.startswith("loctok:h:") else f"loctok:{old}")
    await kv().delete(*keys)


async def new_token(user_id: int, device_id: str, session_token: str | None) -> str:
    """Nyt positionstoken til enheden; det forrige bliver ugyldigt. Kun hashen gemmes – en kopi af Valkey giver ikke
    brugbare tokens. session_token: sessionen, der hentede det – logges den ud, tilbagekaldes positionstokenet."""
    await revoke_token(user_id)
    token = secrets.token_urlsafe(32)
    key = token_key(token)
    await kv().set(key, f"{user_id}:{device_id}", ex=TOKEN_TTL)
    await kv().set(f"loctok:user:{user_id}", key, ex=TOKEN_TTL)
    if session_token:
        await kv().set(_session_key(session_token), str(user_id), ex=TOKEN_TTL)
        await kv().set(f"loctok:user:{user_id}:sess", _session_key(session_token), ex=TOKEN_TTL)
    return token


async def token_owner(token: str) -> str | None:
    """'<bruger-id>:<enheds-id>' for et gyldigt positionstoken, ellers None."""
    # Kun tegn fra secrets.token_urlsafe. Tokenet må aldrig indgå råt i en Valkey-nøgle: før slog en overgangsregel
    # «loctok:<token>» op, så «h:<hash>» ramte den hashede nøgle direkte (en kopi af Valkey gav brugbare tokens), og
    # «user:<id>» slettede pegeren, så tokenet ikke længere kunne tilbagekaldes. Der findes ingen tokens fra før hashingen.
    if not token or len(token) > 100 or not TOKEN_RE.fullmatch(token):
        return None
    return await kv().get(token_key(token))


async def revoke_for_session(session_token: str | None) -> None:
    """Kaldes ved logout: hentede denne session brugerens positionstoken (dvs. det er telefonen, der deler), holder
    telefonen op med at melde positioner. Delingen er stadig slået til og fortsætter, når man logger ind igen."""
    if not session_token:
        return
    user_id = await kv().get(_session_key(session_token))
    if user_id and user_id.isdigit() and await kv().get(f"loctok:user:{user_id}:sess") == _session_key(session_token):
        await revoke_token(int(user_id))
    await kv().delete(_session_key(session_token))


async def forget_near_me(db: AsyncSession, user_id: int) -> None:
    """Fjern Nær mig-områdernes cirkel (geometrien). Områderne og reglerne bliver stående, men matcher intet, indtil
    positionen kendes igen."""
    await db.execute(text("UPDATE areas SET geom = NULL WHERE user_id = :u AND kind = 'radius' AND ref = :me"), {"u": user_id, "me": ME})


def viewing_as(request: Request | None) -> bool:
    """Ser en superadmin appen som en anden bruger? Så må positionen ikke vises."""
    return request is not None and getattr(request.state, "view_as", None) is not None


async def near_me_area_ids(db: AsyncSession, user_id: int) -> set[int]:
    rows = await db.execute(text("SELECT id FROM areas WHERE user_id = :u AND kind = 'radius' AND ref = :me"), {"u": user_id, "me": ME})
    return {r[0] for r in rows}


async def near_me_rule_ids(db: AsyncSession, user_id: int) -> set[int]:
    rows = await db.execute(text("""
        SELECT r.id FROM alert_rules r JOIN areas a ON a.id = r.area_id
        WHERE r.user_id = :u AND a.kind = 'radius' AND a.ref = :me
    """), {"u": user_id, "me": ME})
    return {r[0] for r in rows}


_NEAR = re.compile(r"(near(?::|%3A))-?[0-9.]+(?:,|%2C)-?[0-9.]+", re.IGNORECASE)


def redact(s: str) -> str:
    return _NEAR.sub(r"\1[position]", s)


class RedactPositions(logging.Filter):
    """Fjerner koordinater fra uvicorns access-log (fx /api/feed?scope=near:55.676,12.568,25)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_log_filter() -> None:
    logging.getLogger("uvicorn.access").addFilter(RedactPositions())
