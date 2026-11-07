"""Det samlede datasæt: DOFbasens observationer (observationsgruppen) + DOF.tool's (DOF.tool-gruppen).

De to bor i hver sin database med samme skema (observations, trips, observers og referencedata), og en forespørgsel kan
ikke joine på tværs. Derfor køres den samme SQL i begge grupper:

* Lister uden sideinddeling (Observationer, den enkelte observation, kortets prikker): resultaterne lægges sammen.
* Søgningens sorterede, sideinddelte liste: DOF.tool-gruppens færdige resultatrækker (de er få) sendes med ind i
  DOFbasen-forespørgslens yderste lag, `(<DOFbasen-forespørgslen>) UNION ALL (<DOF.tool-rækkerne med deres typer>)`, så
  sortering (med databasens danske sortering) og sideinddeling er nøjagtig for begge kilder under ét.

DOFbasens tabeller røres aldrig, og DOF.tool-rækkerne lagres ikke i DOFbasens database. Kan DOF.tool-gruppen ikke læses,
vises DOFbasens data alene (fejlen logges)."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from portal.cache import get_json, set_json, version
from portal.db import ToolSession
from portal.services.doftool_obs import VERSION

log = logging.getLogger("portal.dataset")
MAX_ROWS = 50_000  # værn: så mange DOF.tool-rækker tages højst med i én forespørgsel


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


async def tool_fetch(sql: str, params: dict[str, Any], *, setup: tuple[str, ...] = ()) -> tuple[list[dict], list[tuple[str, str]]]:
    """Kør forespørgslen i DOF.tool-gruppen. Returnerer (rækker, [(kolonne, postgres-type), …])."""
    try:
        async with ToolSession() as s:
            for stmt in setup:
                await s.execute(text(stmt))
            res = await s.execute(text(sql), params)
            desc = res.cursor.description
            rows = [dict(r) for r in res.mappings().all()]
            oids = sorted({d[1] for d in desc})
            names = dict((await s.execute(text("SELECT oid::int, format_type(oid, NULL) FROM pg_type WHERE oid = ANY(:o)"),
                                          {"o": oids})).all())
            await s.rollback()
        return rows, [(d[0], names[d[1]]) for d in desc]
    except Exception:  # noqa: BLE001 – DOF.tool-gruppen må aldrig tage DOFbasens data med sig ned
        log.exception("DOF.tool-gruppen kunne ikke læses – viser kun DOFbasens data")
        return [], []


_TABLE = re.compile(r"\b(FROM|JOIN)(\s+)(observations|trips)\b(?:(\s+)(?:AS\s+)?(?!(?:WHERE|JOIN|LEFT|RIGHT|INNER|FULL|CROSS|NATURAL|GROUP|ORDER|ON|USING|LIMIT|UNION|HAVING|WINDOW|OFFSET|FETCH|FOR)\b)([a-z_]\w*))?",
                    re.IGNORECASE)


_TRIP_JOIN = re.compile(r"\b(LEFT\s+)?JOIN\s+trips\s+(?:AS\s+)?(\w+)\s+ON\s+(?:\2\.turid\s*=\s*(\w+)\.turid|(\w+)\.turid\s*=\s*\2\.turid)\b",
                        re.IGNORECASE)


async def tool_data() -> dict[str, list[dict]]:
    """Hele DOF.tool-datasættet {"obs": [...], "trips": [...]} fra DOF.tool-gruppen, cachet pr. version."""
    v = await version(VERSION)
    key = f"doftool:rows:{v}"
    hit = await get_json(key)
    if hit is not None:
        return hit
    obs, _ = await tool_fetch("SELECT * FROM observations", {})
    trips, _ = await tool_fetch("SELECT * FROM trips", {})
    data = {"obs": [{k: _plain(x) for k, x in r.items()} for r in obs], "trips": [{k: _plain(x) for k, x in r.items()} for r in trips]}
    await set_json(key, data, ttl=3600)
    return data


async def include(sql: str, params: dict[str, Any], *, d1: date | None = None, d2: date | None = None,
                  obserkode: str | None = None, obserkoder: list[str] | None = None, loknr: int | None = None,
                  artnr: int | None = None, artnrs: list[int] | None = None) -> tuple[str, dict[str, Any]]:
    """Skriv en læsende forespørgsel om, så `FROM/JOIN observations` og `trips` også rummer DOF.tool-gruppens rækker:
    `(SELECT * FROM observations UNION ALL SELECT * FROM jsonb_populate_recordset(NULL::observations, :rækker))`.
    Til aggregater og forudberegninger, hvor tallene skal være nøjagtige på tværs af de to kilder (antal forskellige
    dage, første fund, rangering …). Argumenterne er en grovsortering af DOF.tool-rækkerne; selve forespørgslen filtrerer
    stadig præcist. Uden DOF.tool-rækker returneres forespørgslen uændret."""
    data = await tool_data()
    a, b = (d1.isoformat() if d1 else None), (d2.isoformat() if d2 else None)
    codes = set(obserkoder) if obserkoder is not None else None
    arts = set(artnrs) if artnrs is not None else None
    rows = [o for o in data["obs"]
            if (a is None or o["dato"] >= a) and (b is None or o["dato"] <= b) and (obserkode is None or o["obserkode"] == obserkode)
            and (codes is None or o["obserkode"] in codes) and (loknr is None or o["loknr"] == loknr)
            and (artnr is None or o["artnr"] == artnr) and (arts is None or o["artnr"] in arts)][:MAX_ROWS]
    if not rows:
        return sql, params
    tur = {o["turid"] for o in rows}
    trips = [t for t in data["trips"] if t["turid"] in tur]

    def repl(m: re.Match) -> str:
        before = sql[max(0, m.start() - 12):m.start()].upper()
        if "DELETE" in before or "INSERT" in before:  # kun læsning – aldrig DELETE FROM / INSERT INTO
            return m.group(0)
        table, alias = m.group(3).lower(), m.group(5)
        src = (f"(SELECT * FROM {table} UNION ALL SELECT * FROM jsonb_populate_recordset(NULL::{table}, "
               f"CAST(:_tool_{table} AS jsonb)))")
        return f"{m.group(1)}{m.group(2)}{src} AS {alias or table}"

    # Join mod turene (`[LEFT] JOIN trips t ON t.turid = o.turid`): som opslag pr. observation, så DOFbasens ture stadig
    # slås op i indekset (en blandet kilde kan Postgres ikke slå op i – den ville læse alle ture). DOF.tool's ture gennemgås
    # kun for DOF.tool's egne observationer (negativt turid; betingelsen er et engangsfilter pr. række).
    def trip_join(m: re.Match) -> str:
        left, t, o = m.group(1) or "", m.group(2), m.group(3) or m.group(4)
        return (f"{left}JOIN LATERAL (SELECT * FROM public.trips WHERE turid = {o}.turid UNION ALL "
                f"SELECT * FROM jsonb_populate_recordset(NULL::trips, CAST(:_tool_trips AS jsonb)) WHERE {o}.turid < 0 AND turid = {o}.turid) "
                f"{t} ON true")

    sql = _TRIP_JOIN.sub(trip_join, sql)
    out = _TABLE.sub(repl, sql)
    return out, {**params, "_tool_observations": json.dumps(rows), "_tool_trips": json.dumps(trips)}


def union_part(rows: list[dict], cols: list[tuple[str, str]], name: str = "_tool") -> tuple[str, dict[str, Any]]:
    """DOF.tool-rækkerne som en SELECT med de rigtige typer, til `(<forespørgsel>) UNION ALL (<denne>)`."""
    defs = ", ".join(f'"{c}" {t}' for c, t in cols)
    names = ", ".join(f'"{c}"' for c, _ in cols)
    payload = json.dumps([{c: _plain(r.get(c)) for c, _ in cols} for r in rows])
    return f"SELECT {names} FROM jsonb_to_recordset(CAST(:{name} AS jsonb)) AS {name}({defs})", {name: payload}


async def execute(session, sql: str, params: dict[str, Any] | None = None, **hints):
    """session.execute(text(sql), params) over hele datasættet (DOFbasen + DOF.tool) – se include()."""
    sql, params = await include(sql, dict(params or {}), **hints)
    return await session.execute(text(sql), params)
