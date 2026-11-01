"""Kodelister til fotos (adfærd, køn, alder, dragt), emneord, rettigheder og sjældenhed. Seedes fra seed/gallery_codes.json;
ukendte tekster fra arkivet oprettes ved import (sort 999) og kan redigeres under Fotos → Admin → Koder."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import GalCode

KINDS = ("adfaerd", "koen", "alder", "dragt")
TAGS = {"Personer": "personer", "Historisk": "historisk", "Tvivl om art": "tvivl_art", "Tvivl om køn/alder/dragt": "tvivl_kad"}
SJAELDENHED = {"almindelig": "Almindelig", "relativ_almindelig": "Relativ almindelig", "ualmindelig": "Ualmindelig", "sjaelden": "Sjælden", "su": "SU"}


@lru_cache(maxsize=1)
def seed_data() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent / "seed" / "gallery_codes.json").read_text(encoding="utf-8"))


def rettigheder() -> dict[int, str]:
    return {int(k): v for k, v in seed_data()["rettigheder"].items()}


def slug(s: str) -> str:
    s = s.casefold().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:16] or "kode"


async def ensure_seed(session: AsyncSession) -> None:
    """Læg kodelisterne ind, hvis de mangler (rører aldrig eksisterende rækker, så admins rettelser bevares)."""
    have = {(k, c) for k, c in (await session.execute(select(GalCode.kind, GalCode.code))).all()}
    for kind in KINDS:
        for i, (code, txt) in enumerate(seed_data()[kind]):
            if (kind, code) not in have:
                session.add(GalCode(kind=kind, code=code, text=txt, sort=(i + 1) * 10))
    await session.flush()


class Codes:
    """Opslag (art, tekst) → kode; opretter ukendte tekster."""

    def __init__(self, rows: list[GalCode]):
        self.by_text = {(r.kind, r.text.casefold()): r.code for r in rows}
        self.used = {(r.kind, r.code) for r in rows}
        self.created: list[tuple[str, str]] = []

    @classmethod
    async def load(cls, session: AsyncSession) -> "Codes":
        await ensure_seed(session)
        return cls(list((await session.execute(select(GalCode))).scalars()))

    def code(self, session: AsyncSession, kind: str, txt: str | None) -> str | None:
        txt = (txt or "").strip()
        if not txt:
            return None
        if (found := self.by_text.get((kind, txt.casefold()))) is not None:
            return found
        base = slug(txt)
        code, n = base, 1
        while (kind, code) in self.used:
            n += 1
            code = f"{base[:14]}{n}"
        session.add(GalCode(kind=kind, code=code, text=txt[:120], sort=999))
        self.used.add((kind, code))
        self.by_text[(kind, txt.casefold())] = code
        self.created.append((kind, txt))
        return code


async def all_codes(session: AsyncSession) -> dict:
    """Til /api/fotos/koder."""
    await ensure_seed(session)
    rows = (await session.execute(select(GalCode).order_by(GalCode.kind, GalCode.sort, GalCode.text))).scalars().all()
    out: dict = {k: [] for k in KINDS}
    for r in rows:
        out.setdefault(r.kind, []).append({"code": r.code, "text": r.text})
    out["emneord"] = [{"code": c, "text": t} for c, t in seed_data()["tags"]]
    out["rettigheder"] = [{"id": k, "text": v} for k, v in rettigheder().items()]
    out["sjaeldenhed"] = [{"code": c, "text": t} for c, t in seed_data()["sjaeldenhed"]]
    out["lande"] = [dict(r) for r in (await session.execute(text("SELECT id, navn, vp FROM gal_countries ORDER BY (netfugl_id = 1) DESC NULLS LAST, navn"))).mappings()]
    return out
