"""Aktivitetslog: sidevisninger og handlinger. Bruges til admin-overblik; slettes efter 180 dage."""

from __future__ import annotations

import json
import logging

from sqlalchemy import text

from portal.db import SessionLocal

log = logging.getLogger("portal.activity")


async def track(user_id: int | None, kind: str, path: str | None = None, detail: dict | None = None) -> None:
    """Gemmes i egen transaktion, så en fejl her aldrig vælter selve handlingen."""
    try:
        async with SessionLocal() as s:
            await s.execute(text("""
                INSERT INTO activity (user_id, kind, path, detail) VALUES (:u, :k, :p, CAST(:d AS jsonb))
            """), {"u": user_id, "k": kind[:32], "p": (path or "")[:200] or None,
                   "d": None if detail is None else json.dumps(detail, default=str)})
            await s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Aktivitet kunne ikke gemmes")


async def overview(session, days: int = 30) -> dict:
    active = (await session.execute(text("""
        SELECT count(DISTINCT user_id) FILTER (WHERE created_at > now() - interval '1 day') AS d1,
               count(DISTINCT user_id) FILTER (WHERE created_at > now() - interval '7 days') AS d7,
               count(DISTINCT user_id) FILTER (WHERE created_at > now() - interval '30 days') AS d30,
               count(*) FILTER (WHERE kind = 'view' AND created_at > now() - interval '1 day') AS views_d1,
               count(*) FILTER (WHERE kind <> 'view' AND created_at > now() - interval '1 day') AS actions_d1
        FROM activity
    """))).mappings().first()
    by_day = (await session.execute(text("""
        SELECT (created_at AT TIME ZONE 'Europe/Copenhagen')::date AS day, count(DISTINCT user_id) AS users,
               count(*) FILTER (WHERE kind = 'view') AS views, count(*) FILTER (WHERE kind <> 'view') AS actions
        FROM activity WHERE created_at > now() - make_interval(days => :d) GROUP BY 1 ORDER BY 1
    """), {"d": days})).mappings().all()
    paths = (await session.execute(text("""
        SELECT regexp_replace(path, '/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]+/[0-9]+|/[0-9]+', '/…', 'g') AS path, count(*) AS n,
               count(DISTINCT user_id) AS users
        FROM activity WHERE kind = 'view' AND created_at > now() - make_interval(days => :d)
        GROUP BY 1 ORDER BY n DESC LIMIT 15
    """), {"d": days})).mappings().all()
    kinds = (await session.execute(text("""
        SELECT kind, count(*) AS n FROM activity WHERE kind <> 'view' AND created_at > now() - make_interval(days => :d)
        GROUP BY 1 ORDER BY n DESC
    """), {"d": days})).mappings().all()
    users = (await session.execute(text("""
        SELECT u.id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode, max(a.created_at) AS last,
               count(*) FILTER (WHERE a.kind = 'view') AS views, count(*) FILTER (WHERE a.kind <> 'view') AS actions
        FROM activity a JOIN users u ON u.id = a.user_id WHERE a.created_at > now() - make_interval(days => :d)
        GROUP BY u.id ORDER BY last DESC LIMIT 50
    """), {"d": days})).mappings().all()
    recent = (await session.execute(text("""
        SELECT a.kind, a.path, a.detail, a.created_at, coalesce(u.navn, u.obserkode, 'anonym') AS navn
        FROM activity a LEFT JOIN users u ON u.id = a.user_id WHERE a.kind <> 'view'
        ORDER BY a.created_at DESC LIMIT 60
    """))).mappings().all()
    return {"active": dict(active), "by_day": [dict(r) for r in by_day], "paths": [dict(r) for r in paths],
            "kinds": [dict(r) for r in kinds], "users": [dict(r) for r in users], "recent": [dict(r) for r in recent]}
