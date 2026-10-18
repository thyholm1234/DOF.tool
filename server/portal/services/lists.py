"""Årslister og scoreboards (Boligbirding/Fugleliga), beregnet direkte i PostgreSQL.

Lister: DK, lokalafdeling, kommune, matrikel (#BB-tag i tur-/fuglenoter), lokalitet og
brugerens egne områder (polygon/radius). År kan være et årstal eller 'all' (livsliste).
Scoreboards caches i Valkey og invalideres når nye observationer for deltagere kommer ind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.models import Setting
from portal.services.areas import Scope, parse_scope


@dataclass(frozen=True)
class ListSpec:
    scope: Scope
    year: int | None  # None = alle år
    matrikel: int | None = None  # 1, 2, ... (kun matrikel-lister)

    @property
    def key(self) -> str:
        return f"{self.scope.key}:{self.year or 'all'}:{self.matrikel or 0}"


def parse_list_spec(scope: str | None, year: str | int | None, matrikel: int | None = None) -> ListSpec:
    y = None if year in (None, "", "all") else int(year)
    return ListSpec(parse_scope(scope), y, matrikel or None)


async def matrikel_prefix(session: AsyncSession) -> str:
    row = await session.get(Setting, "matrikel_tag")
    value = (row.value if row else None) or "#BB"
    return re.sub(r"[^#A-Za-z0-9]", "", str(value)).upper() or "#BB"


def _where(spec: ListSpec, prefix: str) -> tuple[str, dict]:
    pred, params = spec.scope.predicate("o")
    parts = [pred, "s.list_artnr IS NOT NULL", "NOT ls.excluded", "o.antal > 0"]
    if spec.year:
        parts.append("o.dato BETWEEN :y0 AND :y1")
        params.update({"y0": date(spec.year, 1, 1), "y1": date(spec.year, 12, 31)})
    parts.append("o.dato <= current_date")
    if spec.matrikel:
        # #BB26 eller #BB26-1 for matrikel 1, #BB26-2 for matrikel 2 osv. Årstallet skal passe til observationen.
        tag = "(-1)?" if spec.matrikel == 1 else f"-{spec.matrikel}"
        parts.append(
            "upper(coalesce(t.turnoter,'') || ' ' || coalesce(o.fuglnoter,'')) "
            "~ ('(^|[^A-Z0-9])' || :mprefix || to_char(o.dato, 'YY') || :mtag || '([^A-Z0-9-]|$)')"
        )
        params.update({"mprefix": prefix.replace("#", "#"), "mtag": tag})
    return " AND ".join(parts), params


async def year_list(session: AsyncSession, obserkode: str, spec: ListSpec) -> list[dict]:
    prefix = await matrikel_prefix(session)
    where, params = _where(spec, prefix)
    sql = f"""
        SELECT DISTINCT ON (s.list_artnr) s.list_artnr AS artnr, ls.navn AS art, ls.klass, ls.sortering,
               o.dato, o.loknr, l.navn AS lok, o.obsid
        FROM observations o
        JOIN species s ON s.artnr = o.artnr
        JOIN species ls ON ls.artnr = s.list_artnr
        LEFT JOIN trips t ON t.turid = o.turid
        LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE o.obserkode = :kode AND {where}
        ORDER BY s.list_artnr, o.dato, o.obsid
    """
    rows = (await session.execute(text(sql), {"kode": obserkode, **params})).mappings().all()
    out = [{**dict(r), "dato": r["dato"].isoformat()} for r in rows]
    out.sort(key=lambda r: (r["dato"], r["obsid"]))
    return out


async def participants(session: AsyncSession, group_id: int | None = None) -> list[dict]:
    if group_id:
        rows = (await session.execute(text("""
            SELECT m.obserkode, coalesce(u.navn, trim(coalesce(ob.fornavn,'') || ' ' || coalesce(ob.efternavn,'')), m.obserkode) AS navn
            FROM list_group_members m LEFT JOIN users u ON u.obserkode = m.obserkode
            LEFT JOIN observers ob ON ob.obserkode = m.obserkode WHERE m.group_id = :g
        """), {"g": group_id})).all()
    else:
        rows = (await session.execute(text("""
            SELECT obserkode, coalesce(navn, obserkode) FROM users
            WHERE NOT blocked AND coalesce((prefs->>'lists_public')::boolean, true)
        """))).all()
    return [{"obserkode": r[0], "navn": r[1] or r[0]} for r in rows]


async def scoreboard(session: AsyncSession, spec: ListSpec, group_id: int | None = None) -> dict:
    """Rangliste + hver deltagers 'firsts' (til race-graf, matrix og unikke arter)."""
    key = f"sb:{spec.key}:{group_id or 0}:{await version('lists')}"

    async def produce() -> dict:
        people = await participants(session, group_id)
        if spec.scope.kind == "afdeling" and not group_id:
            # Afdelingsboards: kun deltagere der har valgt afdelingen (som i Boligbirding)
            opted = await _opted(session, "afdelinger", spec.scope.ref)
            people = [p for p in people if p["obserkode"] in opted]
        if spec.scope.kind == "kommune" and not group_id:
            opted = await _opted(session, "kommuner", spec.scope.ref)
            people = [p for p in people if p["obserkode"] in opted]
        codes = [p["obserkode"] for p in people]
        if not codes:
            return {"rows": [], "species": {}, "spec": spec.key}
        prefix = await matrikel_prefix(session)
        where, params = _where(spec, prefix)
        firsts = (await session.execute(text(f"""
            SELECT o.obserkode, s.list_artnr AS artnr, min(o.dato) AS d
            FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
            LEFT JOIN trips t ON t.turid = o.turid
            WHERE o.obserkode = ANY(:codes) AND {where}
            GROUP BY o.obserkode, s.list_artnr
        """), {"codes": codes, **params})).all()
        per: dict[str, list[tuple[int, str]]] = {c: [] for c in codes}
        for kode, artnr, d in firsts:
            per[kode].append((artnr, d.isoformat()))
        species_ids = {a for lst in per.values() for a, _ in lst}
        names = {}
        if species_ids:
            names = {r[0]: r[1] for r in (await session.execute(
                text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": list(species_ids)})).all()}
        rows = []
        for p in people:
            lst = sorted(per[p["obserkode"]], key=lambda x: x[1])
            last = lst[-1] if lst else None
            rows.append({
                "obserkode": p["obserkode"], "navn": p["navn"], "antal": len(lst),
                "sidste_art": names.get(last[0]) if last else None, "sidste_dato": last[1] if last else None,
                "firsts": lst,
            })
        rows.sort(key=lambda r: (-r["antal"], r["sidste_dato"] or "9999"))
        place = 0
        prev = None
        for i, r in enumerate(rows, start=1):
            if r["antal"] != prev:
                place = i
                prev = r["antal"]
            r["placering"] = place
        return {"rows": rows, "species": {str(k): v for k, v in names.items()}, "spec": spec.key}

    return await cached(key, 6 * 3600, produce)


async def _opted(session: AsyncSession, field: str, ref: str) -> set[str]:
    rows = (await session.execute(text(f"""
        SELECT obserkode FROM users WHERE prefs->'{field}' ? :ref
    """), {"ref": str(ref)})).all()
    return {r[0] for r in rows}


async def available_years(session: AsyncSession, obserkode: str) -> list[int]:
    rows = (await session.execute(text("""
        SELECT DISTINCT extract(year FROM dato)::int FROM observations WHERE obserkode = :k ORDER BY 1 DESC
    """), {"k": obserkode})).all()
    return [r[0] for r in rows]


async def matrikel_numbers(session: AsyncSession, obserkode: str) -> list[int]:
    prefix = await matrikel_prefix(session)
    rows = (await session.execute(text("""
        SELECT DISTINCT coalesce(nullif(substring(upper(t.turnoter) FROM :rx), ''), '1')
        FROM trips t WHERE t.obserkode = :k AND upper(t.turnoter) LIKE :like
    """), {"k": obserkode, "rx": f"{re.escape(prefix)}[0-9]{{2}}-([0-9])", "like": f"%{prefix}%"})).all()
    nums = sorted({int(r[0]) for r in rows if r[0] and r[0].isdigit()})
    return nums or [1]
