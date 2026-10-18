"""Kategorisering af observationer: SU / SUB / bemaerk / alm.

Samme regler som DOF.not:
1. SU/SUB fra artsklassifikationen har forrang.
2. Datoen ligger i artens 'bemærk'-vindue (sommer-/vinterforekomst) → bemaerk.
3. Antal ≥ lokalafdelingens bemærk-tærskel i mindst én af observationens afdelinger → bemaerk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import version
from portal.models import Afdeling, BemaerkThreshold, PhenologyWindow, Species

NOTABLE = ("SU", "SUB", "bemaerk")


def _ddmm(value: str) -> tuple[int, int]:
    d, m = value.split("-")
    return int(m), int(d)


@dataclass
class Classifier:
    klass_by_artnr: dict[int, str] = field(default_factory=dict)
    windows: dict[str, list[tuple[tuple[int, int], tuple[int, int]]]] = field(default_factory=dict)
    thresholds: dict[int, dict[str, int]] = field(default_factory=dict)
    afdeling_ids: dict[str, int] = field(default_factory=dict)
    ref_version: int = -1

    def afdeling_id(self, navn: str) -> int | None:
        return self.afdeling_ids.get(navn.strip().lower())

    def in_window(self, artnavn: str, dato: date) -> bool:
        md = (dato.month, dato.day)
        for start, end in self.windows.get(artnavn, ()):
            if start <= end:
                if start <= md <= end:
                    return True
            elif md >= start or md <= end:
                return True
        return False

    def kategori(self, artnr: int, artnavn: str, dato: date, antal: int, afdeling_ids: list[int]) -> str:
        klass = self.klass_by_artnr.get(artnr)
        if klass == "SU":
            return "SU"
        if klass == "SUB":
            return "SUB"
        if self.in_window(artnavn, dato):
            return "bemaerk"
        for afd in afdeling_ids:
            threshold = self.thresholds.get(afd, {}).get(artnavn)
            if threshold is not None and antal >= threshold:
                return "bemaerk"
        return "alm"


_cached = Classifier()


async def get_classifier(session: AsyncSession) -> Classifier:
    """Genindlæses kun når referencedata har ændret version."""
    global _cached
    current = await version("reference")
    if _cached.ref_version == current and _cached.afdeling_ids:
        return _cached
    c = Classifier(ref_version=current)
    for artnr, klass in (await session.execute(select(Species.artnr, Species.klass))).all():
        c.klass_by_artnr[artnr] = klass
    for navn, fra, til in (await session.execute(select(PhenologyWindow.artnavn, PhenologyWindow.fra, PhenologyWindow.til))).all():
        try:
            c.windows.setdefault(navn, []).append((_ddmm(fra), _ddmm(til)))
        except ValueError:
            continue
    for afd, navn, antal in (await session.execute(select(BemaerkThreshold.afdeling_id, BemaerkThreshold.artnavn, BemaerkThreshold.antal))).all():
        c.thresholds.setdefault(afd, {})[navn] = antal
    for afd_id, navn in (await session.execute(select(Afdeling.id, Afdeling.navn))).all():
        c.afdeling_ids[navn.lower()] = afd_id
    _cached = c
    return c
