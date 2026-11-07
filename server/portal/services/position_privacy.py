"""Værn om brugernes position (baggrundspositionen fra appen og 'Nær mig').

Positionen er den mest følsomme oplysning i portalen: den viser, hvor en person er – også når appen er lukket. Reglerne:

* Positionen gemmes afrundet (ca. 100 m) – mere præcision skal 'Nær mig' ikke bruge.
* Positionstokenet ligger kun som SHA-256 i Valkey, så en kopi af Valkey (backup, dump) ikke kan bruges til at melde
  positioner i en brugers navn.
* Ingen andre end brugeren selv ser positionen – heller ikke en superadmin i «se som bruger». Det gælder også alt, der
  kan afsløre den: 'Nær mig'-områdets geometri, feed og lister for området og notifikationer fra regler på området.
* Koordinater i adresser (scope=near:…) skrives ikke i access-loggen."""

from __future__ import annotations

import hashlib
import logging
import re

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ME = "me"
DECIMALS = 3  # ca. 111 m nord-syd, 65 m øst-vest i Danmark


def coarse(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, DECIMALS), round(lon, DECIMALS)


def token_key(token: str) -> str:
    """Valkey-nøglen for et positionstoken: kun hashen gemmes."""
    return "loctok:h:" + hashlib.sha256(token.encode()).hexdigest()


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
