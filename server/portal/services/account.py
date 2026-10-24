"""Brugerens rettigheder efter GDPR: udtræk af egne data (art. 15/20) og fuld sletning af kontoen (art. 17).

Udtrækket findes ud fra skemaet: alle tabeller i brugergruppen med en kolonne, der peger på users.id, så nye tabeller
automatisk kommer med. Hemmeligheder (adgangskode-hash, token-hash, kalendertoken) udelades.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete as sql_delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.db import Base
from portal.dbgroups import USER_TABLES
from portal.models import User

SECRET_COLUMNS = {"test_pw_hash", "token_hash"}
SECRET_PREFS = {"ics_token"}


def _user_refs() -> list[tuple[str, str]]:
    """(tabel, kolonne) for alle brugerdata-kolonner med fremmednøgle til users.id."""
    out = []
    for t in Base.metadata.sorted_tables:
        if t.name not in USER_TABLES or t.name == "users":
            continue
        for c in t.columns:
            if any(fk.column.table.name == "users" and fk.column.name == "id" for fk in c.foreign_keys):
                out.append((t.name, c.name))
    return out


def _plain(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, memoryview)):
        return None  # geometri o.l. – kun i den læsbare form nedenfor
    return v


async def export(db: AsyncSession, user: User) -> dict:
    me = {c.name: _plain(getattr(user, c.key)) for c in User.__table__.columns if c.name not in SECRET_COLUMNS}
    me["prefs"] = {k: v for k, v in (user.prefs or {}).items() if k not in SECRET_PREFS}
    data: dict[str, list] = {}
    for table, col in _user_refs():
        cols = [c.name for c in Base.metadata.tables[table].columns if c.name not in SECRET_COLUMNS]
        rows = (await db.execute(text(f'SELECT * FROM "{table}" WHERE "{col}" = :u'), {"u": user.id})).mappings().all()
        rows = [{k: _plain(r[k]) for k in cols if k in r} for r in rows]
        if rows:
            data.setdefault(table, []).extend({"_relation": col, **r} if col != "user_id" else r for r in rows)
    # Tegnede områder som GeoJSON i stedet for binær geometri
    geo = (await db.execute(text("SELECT id, ST_AsGeoJSON(geom, 6) FROM areas WHERE user_id = :u"), {"u": user.id})).all()
    for row in data.get("areas", []):
        row["geom"] = next((g for i, g in geo if i == row.get("id")), None)
    return {
        "info": "Udtræk af dine data i DOF.tool (art. 15 og 20 i databeskyttelsesforordningen). Observationer, du har "
                "indtastet i DOFbasen, ligger hos DOF og kan hentes dér.",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user": me,
        "data": data,
    }


def _media_path(url: str | None) -> Path | None:
    root = Path(get_settings().media_dir).resolve()
    if not url or not url.startswith("/media/"):
        return None
    p = (root / url.removeprefix("/media/")).resolve()
    return p if root in p.parents else None


async def delete(db: AsyncSession, user: User) -> None:
    """Slet kontoen med alt tilknyttet: rækker (kaskade), aktivitetslog, uploadede filer og alle sessioner."""
    urls = [m.get("url") for (media,) in (await db.execute(text("SELECT media FROM field_reports WHERE user_id = :u"),
                                                             {"u": user.id})).all() for m in (media or [])]
    urls += list((await db.execute(text("SELECT url FROM nf_photos WHERE user_id = :u"), {"u": user.id})).scalars())
    await db.execute(text("DELETE FROM activity WHERE user_id = :u"), {"u": user.id})
    await db.execute(sql_delete(User).where(User.id == user.id))
    await db.commit()
    for url in urls:
        p = _media_path(url)
        if p and p.is_file():
            p.unlink()
    await destroy_sessions(user.id)


async def destroy_sessions(user_id: int) -> None:
    key = f"usess:{user_id}"
    tokens = await kv().smembers(key)
    if tokens:
        await kv().delete(*[f"sess:{t.decode() if isinstance(t, bytes) else t}" for t in tokens])
    await kv().delete(key)

