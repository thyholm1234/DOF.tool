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
    """Læg kodelisterne ind, hvis en slags ikke har nogen koder endnu (rører aldrig eksisterende lister, så admins rettelser og sletninger bevares)."""
    have = {k for (k,) in (await session.execute(select(GalCode.kind).distinct())).all()}
    for kind in KINDS:
        if kind in have:
            continue
        for i, (code, txt) in enumerate(seed_data()[kind]):
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


# ---------------------------------------------------------------- admin → Koder

COL = {"adfaerd": "adfaerd", "koen": "koen", "alder": "alder", "dragt": "dragt"}


async def admin_list(session: AsyncSession) -> list[dict]:
    await ensure_seed(session)
    used: dict[tuple[str, str], int] = {}
    for kind, col in COL.items():
        for code, n in (await session.execute(text(f"SELECT {col}, count(*) FROM gal_birds WHERE {col} IS NOT NULL GROUP BY 1"))).all():
            used[(kind, code)] = n
    rows = (await session.execute(select(GalCode).order_by(GalCode.kind, GalCode.sort, GalCode.text))).scalars().all()
    return [{"kind": r.kind, "code": r.code, "text": r.text, "sort": r.sort, "dofbasen_code": r.dofbasen_code, "brugt": used.get((r.kind, r.code), 0)} for r in rows]


async def admin_save(session: AsyncSession, items: list[dict]) -> None:
    """Ret tekst, sortering og DOFbasen-kode; nye koder oprettes (kind + code + text)."""
    for it in items:
        kind, code = it.get("kind"), str(it.get("code") or "").strip()
        if kind not in KINDS:
            raise ValueError("Ukendt slags kode")
        txt = str(it.get("text") or "").strip()
        if not txt:
            raise ValueError("Teksten må ikke være tom")
        row = await session.get(GalCode, (kind, code)) if code else None
        if row is None:
            code = code or slug(txt)
            if await session.get(GalCode, (kind, code)):
                raise ValueError(f"Koden {code} findes allerede")
            row = GalCode(kind=kind, code=code[:16], text=txt[:120])
            session.add(row)
        row.text, row.sort = txt[:120], int(it.get("sort", row.sort or 100))
        row.dofbasen_code = (str(it.get("dofbasen_code") or "").strip()[:16]) or None
    await session.commit()


async def admin_merge(session: AsyncSession, kind: str, from_code: str, into_code: str) -> int:
    if kind not in KINDS or from_code == into_code:
        raise ValueError("Ugyldig sammenlægning")
    if await session.get(GalCode, (kind, from_code)) is None or await session.get(GalCode, (kind, into_code)) is None:
        raise LookupError("Koden findes ikke")
    n = (await session.execute(text(f"UPDATE gal_birds SET {COL[kind]} = :i WHERE {COL[kind]} = :f"), {"i": into_code, "f": from_code})).rowcount
    await session.execute(text("DELETE FROM gal_codes WHERE kind = :k AND code = :f"), {"k": kind, "f": from_code})
    await session.commit()
    return n
