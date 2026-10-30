"""Manuelle fund (Moderation → Arter → Kategorier): fund der ikke står i DOFbasen, spejlet ind i observations med obsid = -id.

Spejlingen gør, at fundet tæller som en almindelig observation alle steder, der læser observations (Arter pr. år,
lokaliteten, artens side, tråden). Det har ingen obserkode og kommer derfor ikke på nogens personlige liste.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, worker_command
from portal.models import ManualFind
from portal.services.setting_store import put_setting, setting_exists


async def mirror(session: AsyncSession, f: ManualFind) -> None:
    """Læg fundet ind i observations (eller fjern det, hvis det er slettet)."""
    await session.execute(text("DELETE FROM observations WHERE obsid = :o"), {"o": -f.id})
    if f.removed_at is None:
        sted = f"Manuelt fund{': ' + f.sted if f.sted else ''}."
        await session.execute(text("""
            INSERT INTO observations (obsid, dato, artnr, antal, loknr, afdelinger, fuglnoter, kategori, hemmelig)
            SELECT :o, :d, :a, :n, :l,
                   coalesce((SELECT ARRAY[afdeling_id]::smallint[] FROM locations WHERE loknr = :l AND afdeling_id IS NOT NULL), '{}'),
                   :noter, coalesce((SELECT CASE klass WHEN 'SU' THEN 'SU' WHEN 'SUB' THEN 'SUB' END FROM species WHERE artnr = :a), 'alm'), 0
        """), {"o": -f.id, "d": f.dato, "a": f.artnr, "n": f.antal, "l": f.loknr,
               "noter": f"{sted} {f.note}".strip() if f.note else sted})


async def changed(f: ManualFind) -> None:
    """Efter oprettelse/sletning: caches og de forudberegnede tal fra fundets år (og lokaliteten det år)."""
    for v in ("lists", "regions", "feed"):
        await bump(v)
    await worker_command("regions", from_year=f.dato.year)
    if f.loknr:
        await worker_command("location_years", years=[f.dato.year])


# Fund fra club300.dk's DK årsarter 2021–2025, som ikke står i DOFbasen. Lægges ind én gang ved opstart.
SEED_KEY = "manual_finds_seed_v1"
SEED = [
    {"latin": "Thalassarche melanophris", "dato": "2022-04-02", "antal": 1, "lok": None, "sted": "60 km VNV for Hanstholm",
     "note": "Fra club300.dk (DK årsarter 2022) – fund til havs, ikke i DOFbasen"},
    {"latin": "Antigone canadensis", "dato": "2025-04-02", "antal": 1, "lok": "Ringfenner", "sted": None,
     "note": "Fra club300.dk (DK årsarter 2025) – ikke i DOFbasen"},
]


async def seed(session: AsyncSession) -> None:
    if await setting_exists(session, SEED_KEY):
        return
    if not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM species)"))).scalar():
        return
    added = []
    for s in SEED:
        artnr = (await session.execute(text("SELECT artnr FROM species WHERE latin = :l AND art_type = 'art'"), {"l": s["latin"]})).scalar()
        loknr = (await session.execute(text("SELECT loknr FROM locations WHERE navn = :n ORDER BY loknr LIMIT 1"), {"n": s["lok"]})).scalar() \
            if s["lok"] else None
        if artnr is None or (s["lok"] and loknr is None):
            continue
        f = ManualFind(artnr=artnr, dato=date.fromisoformat(s["dato"]), antal=s["antal"], loknr=loknr, sted=s["sted"], note=s["note"])
        session.add(f)
        await session.flush()
        await mirror(session, f)
        added.append(f)
    await put_setting(session, SEED_KEY, {"added": [f.id for f in added]})
    for f in added:
        await changed(f)
