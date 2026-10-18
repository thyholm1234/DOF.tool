"""Import af lokale månedsudtræk (dofbasen_ÅÅÅÅ_MM.csv) – helt uden kald til DOFbasen."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select, text

from portal.cache import bump, kv
from portal.config import get_settings
from portal.db import SessionLocal
from portal.dof.ingest import ingest
from portal.dof.parse import iter_records, parse_rows
from portal.models import ArchiveFile

log = logging.getLogger("portal.archive")
FILE_RE = re.compile(r"^dofbasen_(\d{4})_(\d{2})\.csv$")
CHUNK = 40_000
PROGRESS = "archive:progress"


def archive_files() -> list[Path]:
    root = Path(get_settings().archive_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if FILE_RE.match(p.name))


async def pending_files() -> list[Path]:
    files = archive_files()
    async with SessionLocal() as s:
        done = {r.name: r for r in (await s.execute(select(ArchiveFile))).scalars()}
    out = []
    for p in files:
        st = p.stat()
        rec = done.get(p.name)
        if rec is None or rec.size != st.st_size or abs(rec.mtime - st.st_mtime) > 1:
            out.append(p)
    return out


async def import_file(path: Path) -> tuple[int, date | None, date | None]:
    st = path.stat()
    total = 0
    dmin: date | None = None
    dmax: date | None = None
    with path.open("rb") as fh:
        stream = io.TextIOWrapper(fh, encoding="latin-1", newline="")
        records = iter_records(stream)
        def next_chunk():
            chunk = []
            for rec in records:
                chunk.append(rec)
                if len(chunk) >= CHUNK:
                    break
            return len(chunk), parse_rows(chunk)

        while True:
            # Læsning/parsing i tråd, så gateway og planlægger i samme event-loop ikke blokeres
            n_read, rows = await asyncio.to_thread(next_chunk)
            if not n_read:
                break
            if rows:
                lo = min(r.dato for r in rows)
                hi = max(r.dato for r in rows)
                dmin = lo if dmin is None or lo < dmin else dmin
                dmax = hi if dmax is None or hi > dmax else dmax
            async with SessionLocal() as s:
                await ingest(s, rows, mode="archive")
                await s.commit()
            total += len(rows)
            await kv().hset(PROGRESS, mapping={"file": path.name, "rows_in_file": total})
            if n_read < CHUNK:
                break
    async with SessionLocal() as s:
        await s.execute(text("""
            INSERT INTO archive_files (name, size, mtime, rows) VALUES (:n, :s, :m, :r)
            ON CONFLICT (name) DO UPDATE SET size = EXCLUDED.size, mtime = EXCLUDED.mtime, rows = EXCLUDED.rows, imported_at = now()
        """), {"n": path.name, "s": st.st_size, "m": st.st_mtime, "r": total})
        if dmin and dmax:
            # Dage der er dækket komplet af arkivet (sidste dag i en løbende måned kan være ufuldstændig)
            last_complete = dmax - timedelta(days=1)
            await s.execute(text("""
                INSERT INTO day_sync (dato, rows, source)
                SELECT d::date, 0, 'archive' FROM generate_series(CAST(:a AS date), CAST(:b AS date), interval '1 day') d
                ON CONFLICT (dato) DO NOTHING
            """), {"a": dmin, "b": last_complete})
        await s.commit()
    return total, dmin, dmax


async def run_import(stop_after: int | None = None) -> dict:
    """Importér alle nye/ændrede arkivfiler. Fremdrift ligger i Valkey (archive:progress)."""
    r = kv()
    if not await r.set("archive:lock", "1", nx=True, ex=6 * 3600):
        return {"started": False, "reason": "kører allerede"}
    try:
        files = await pending_files()
        if stop_after:
            files = files[:stop_after]
        await r.delete(PROGRESS)
        await r.hset(PROGRESS, mapping={"status": "running", "files_total": len(files), "files_done": 0,
                                        "rows_total": 0, "started": time.time()})
        rows_total = 0
        earliest: date | None = None
        for i, path in enumerate(files, start=1):
            t0 = time.time()
            n, dmin, _ = await import_file(path)
            rows_total += n
            if dmin and (earliest is None or dmin < earliest):
                earliest = dmin
            await r.hset(PROGRESS, mapping={"files_done": i, "rows_total": rows_total, "last_file": path.name})
            log.info("Arkiv %s: %s rækker (%.1fs)", path.name, n, time.time() - t0)
        await r.hset(PROGRESS, mapping={"status": "aggregating"})
        if earliest:
            from portal.services.phenology import refresh_aggregates
            async with SessionLocal() as s:
                await refresh_aggregates(s, earliest)
        await bump("lists")
        await bump("feed")
        await r.hset(PROGRESS, mapping={"status": "done", "finished": time.time()})
        return {"started": True, "files": len(files), "rows": rows_total}
    except Exception as exc:
        await r.hset(PROGRESS, mapping={"status": "error", "error": str(exc)})
        raise
    finally:
        await r.delete("archive:lock")
