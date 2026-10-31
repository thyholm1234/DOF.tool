"""Observatørsider (#/observator/KODE): en observatørs tal, år for år, foretrukne lokaliteter, seneste bemærkelsesværdige
fund og ture – ud fra DOFbasen-observationerne.

Privatliv: hemmelige observationer indgår aldrig. Portalbrugere kan skjule deres side (prefs.profile_public = false);
så ser kun de selv og administratorer den. Observatører, der ikke bruger portalen, vises som på dofbasen.dk. By og
kontaktoplysninger vises ikke.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached
from portal.domain import kategori
from portal.models import User
from portal.services.locyear import COUNTED
from portal.timeutil import dk_today

TTL = 3600
_FROM = """FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN species ls ON ls.artnr = s.list_artnr
           WHERE o.obserkode = :k AND o.hemmelig = 0"""


def visible_to(user_row: dict | None, viewer: User | None) -> bool:
    """Må viewer se observatørsiden? Kun portalbrugere kan skjule den (DOFbasen-observatører vises som på dofbasen.dk)."""
    if user_row is None or (user_row.get("prefs") or {}).get("profile_public", True):
        return True
    return viewer is not None and (viewer.id == user_row["id"] or viewer.role in ("admin", "superadmin"))


async def _portal_user(session: AsyncSession, kode: str) -> dict | None:
    row = (await session.execute(text("SELECT id, navn, created_at, prefs FROM users WHERE obserkode = :k AND NOT blocked"),
                                 {"k": kode})).mappings().first()
    return dict(row) if row else None


async def _stats(session: AsyncSession, kode: str) -> dict:
    async def produce():
        a = {"k": kode}
        tot = (await session.execute(text(f"""
            SELECT count(*) AS n_obs, count(DISTINCT o.turid) AS n_ture, count(DISTINCT o.dato) AS n_dage,
                   count(DISTINCT o.loknr) AS n_lok, min(o.dato) AS first, max(o.dato) AS last,
                   count(DISTINCT s.list_artnr) FILTER (WHERE {COUNTED}) AS n_arter {_FROM}"""), a)).mappings().first()
        years = (await session.execute(text(f"""
            SELECT extract(year FROM o.dato)::int AS aar, count(*) AS n_obs, count(DISTINCT o.dato) AS n_dage,
                   count(DISTINCT s.list_artnr) FILTER (WHERE {COUNTED}) AS n_arter {_FROM} GROUP BY 1 ORDER BY 1"""), a)).mappings().all()
        places = (await session.execute(text("""
            SELECT o.loknr, l.navn, count(DISTINCT o.dato) AS dage, max(o.dato) AS senest
            FROM observations o JOIN locations l ON l.loknr = o.loknr WHERE o.obserkode = :k AND o.hemmelig = 0
            GROUP BY o.loknr, l.navn ORDER BY dage DESC, senest DESC LIMIT 12"""), a)).mappings().all()
        notable = (await session.execute(text(f"""
            SELECT o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lok, max({kategori.rank_sql("o.kategori")}) AS rank, max(o.antal) AS antal
            FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
            WHERE o.obserkode = :k AND o.hemmelig = 0 AND o.kategori IN ('SU', 'SUB', 'bemaerk') AND o.dato > :since
            GROUP BY o.dato, o.artnr, s.navn, o.loknr, l.navn ORDER BY o.dato DESC, rank DESC LIMIT 30"""),
            {**a, "since": dk_today().replace(year=dk_today().year - 1)})).mappings().all()
        trips = (await session.execute(text("""
            SELECT o.turid, o.dato, o.loknr, l.navn AS lok, count(DISTINCT o.artnr) AS arter, count(*) AS obs
            FROM observations o JOIN locations l ON l.loknr = o.loknr
            WHERE o.obserkode = :k AND o.hemmelig = 0 AND o.turid IS NOT NULL
            GROUP BY o.turid, o.dato, o.loknr, l.navn ORDER BY o.dato DESC, o.turid DESC LIMIT 12"""), a)).mappings().all()
        iso = lambda v: v.isoformat() if v else None  # noqa: E731
        return {
            "tal": {**{k: tot[k] for k in ("n_obs", "n_ture", "n_dage", "n_lok", "n_arter")}, "first": iso(tot["first"]), "last": iso(tot["last"])},
            "aar": [dict(r) for r in years],
            "lokaliteter": [{**dict(r), "senest": iso(r["senest"])} for r in places],
            "fund": [{**{k: r[k] for k in ("artnr", "art", "loknr", "lok", "antal")}, "dato": iso(r["dato"]),
                      "kategori": kategori.from_rank(r["rank"])} for r in notable],
            "ture": [{**dict(r), "dato": iso(r["dato"])} for r in trips],
        }
    # En time gammel er godt nok (feedets version skifter ved hver live-sync – så ville de store sider altid regnes forfra)
    return await cached(f"obsprof:{kode}:{dk_today()}", TTL, produce)


async def profile(session: AsyncSession, kode: str, viewer: User | None) -> dict | None:
    """Observatørsiden, eller None hvis obserkoden er ukendt. {"skjult": True} hvis brugeren har skjult den."""
    kode = kode.strip().upper()[:16]
    ob = (await session.execute(text("SELECT obserkode, fornavn, efternavn FROM observers WHERE obserkode = :k"),
                                {"k": kode})).mappings().first()
    user = await _portal_user(session, kode)
    if ob is None and user is None:
        return None
    navn = (user or {}).get("navn") or " ".join(x for x in ((ob or {}).get("fornavn"), (ob or {}).get("efternavn")) if x) or kode
    base = {"obserkode": kode, "navn": navn, "portalbruger": user is not None, "mig": bool(viewer and user and viewer.id == user["id"])}
    if not visible_to(user, viewer):
        return {**base, "skjult": True}
    out = {**base, "skjult": False, **(await _stats(session, kode) if ob else {"tal": None, "aar": [], "lokaliteter": [], "fund": [], "ture": []})}
    if user:
        afd_ids = [int(x) for x in (user.get("prefs") or {}).get("afdelinger") or [] if str(x).isdigit()]
        names = dict((await session.execute(text("SELECT id, navn FROM afdelinger WHERE id = ANY(:i)"), {"i": afd_ids})).all()) if afd_ids else {}
        out["portal"] = {"siden": user["created_at"].date().isoformat() if user["created_at"] else None,
                         "afdelinger": [names[i] for i in afd_ids if i in names],
                         "offentlig": (user.get("prefs") or {}).get("profile_public", True)}
    return out


async def search(session: AsyncSession, q: str, viewer: User | None, limit: int = 20) -> list[dict]:
    """Observatører efter navn eller obserkode (skjulte portalprofiler er udeladt)."""
    q = q.strip()
    rows = (await session.execute(text("""
        SELECT obserkode, trim(coalesce(fornavn, '') || ' ' || coalesce(efternavn, '')) AS navn FROM observers
        WHERE obserkode ILIKE :p OR (coalesce(fornavn, '') || ' ' || coalesce(efternavn, '')) ILIKE :q
        ORDER BY (obserkode ILIKE :p) DESC, efternavn, fornavn LIMIT :n"""), {"p": f"{q}%", "q": f"%{q}%", "n": limit * 2})).mappings().all()
    kodes = [r["obserkode"] for r in rows]
    hidden = set((await session.execute(text("""SELECT obserkode FROM users WHERE obserkode = ANY(:k) AND NOT blocked
        AND (prefs->>'profile_public')::boolean IS FALSE"""), {"k": kodes})).scalars().all()) if kodes else set()
    if viewer and viewer.role in ("admin", "superadmin"):
        hidden = set()
    hidden.discard(viewer.obserkode if viewer else None)
    return [{"obserkode": r["obserkode"], "navn": r["navn"] or r["obserkode"]} for r in rows if r["obserkode"] not in hidden][:limit]
