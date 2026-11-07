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
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from portal.db import ToolSession

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


def union_part(rows: list[dict], cols: list[tuple[str, str]], name: str = "_tool") -> tuple[str, dict[str, Any]]:
    """DOF.tool-rækkerne som en SELECT med de rigtige typer, til `(<forespørgsel>) UNION ALL (<denne>)`."""
    defs = ", ".join(f'"{c}" {t}' for c, t in cols)
    names = ", ".join(f'"{c}"' for c, _ in cols)
    payload = json.dumps([{c: _plain(r.get(c)) for c, _ in cols} for r in rows])
    return f"SELECT {names} FROM jsonb_to_recordset(CAST(:{name} AS jsonb)) AS {name}({defs})", {name: payload}
