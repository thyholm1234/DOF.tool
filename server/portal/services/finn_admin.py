"""Admin → Tællinger → FiNN-områder: ret områder og punkter, og synkronisér med DOFbasen.

En synkronisering henter DOFbasens områder og punkter (services/finn.fetch) og gemmer dem med en ændringsrapport
(finn_syncs). Intet ændres, før admin har set rapporten og valgt, hvad der skal anvendes – som lokalitetsimporten:

* nye – findes hos DOFbasen, ikke i portalen
* aendrede – DOFbasen har ændret noget, portalen ikke har rettet: portalen følger med
* konflikter – DOFbasen har ændret noget, portalen også har rettet: admin vælger «behold portalens» eller «brug DOFbasens»
* fjernede – væk hos DOFbasen (eller blevet testområde): skjules i appen og markeres, men slettes ikke

Hver række husker DOFbasens seneste version i `dof`; «ændret hos DOFbasen» er altså målt mod den, ikke mod portalens
gældende version. Portalens egne områder (origin = portal) røres aldrig af en synkronisering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import FinnPoint, FinnSite, FinnSync
from portal.services import finn

LOCAL_ID0 = 1_000_000  # portalens egne områder og punkter
SITE_FIELDS = ("title", "loknr", "ring")
POINT_FIELDS = ("site", "lat", "lon", "source", "type")
GROUPS = ("nye", "aendrede", "konflikter", "fjernede")
EXAMPLES = 25


class FinnError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _site_dof(s: dict) -> dict:
    return {"title": s["title"], "loknr": s.get("loknr"), "ring": s["ring"]}


def _point_dof(p: dict) -> dict:
    return {k: p.get(k) for k in POINT_FIELDS}


def _site_cur(r: FinnSite) -> dict:
    return {"title": r.title, "loknr": r.loknr, "ring": r.ring}


def _point_cur(r: FinnPoint) -> dict:
    return {"site": r.site_id, "lat": r.lat, "lon": r.lon, "source": r.source, "type": r.type}


def _what(old: dict | None, new: dict, fields: tuple[str, ...]) -> list[str]:
    """Hvilke felter er forskellige (grænsen sammenlignes punkt for punkt)."""
    return [f for f in fields if (old or {}).get(f) != new.get(f)]


def diff(fetched: dict, sites: list[FinnSite], points: list[FinnPoint]) -> dict:
    """Ændringsrapporten: {"sites": {gruppe: [{id, title, felter}]}, "points": {…}}. Ren funktion (testbar uden database)."""
    out: dict[str, Any] = {}
    for kind, rows, items, key_dof, cur, fields, label in (
        ("sites", sites, fetched["sites"], _site_dof, _site_cur, SITE_FIELDS, lambda x: x.get("title")),
        ("points", points, fetched["points"], _point_dof, _point_cur, POINT_FIELDS, lambda x: f"Punkt {x.get('id')}"),
    ):
        have = {r.id: r for r in rows if r.origin == "dofbasen"}
        g: dict[str, list] = {k: [] for k in GROUPS}
        seen = set()
        for it in items:
            seen.add(it["id"])
            new = key_dof(it)
            r = have.get(it["id"])
            if r is None:
                g["nye"].append({"id": it["id"], "title": label(it)})
                continue
            changed = _what(r.dof, new, fields)
            if r.removed_at is not None:  # tilbage hos DOFbasen: behandles som ændret
                changed = changed or ["genopstået"]
            if not changed:
                continue
            row = {"id": r.id, "title": label(it) if kind == "sites" else f"Punkt {r.id}", "felter": changed}
            if r.edited and _what(_site_cur(r) if kind == "sites" else _point_cur(r), new, fields):
                g["konflikter"].append(row)
            else:
                g["aendrede"].append(row)
        for r in have.values():
            if r.id not in seen and r.removed_at is None:
                g["fjernede"].append({"id": r.id, "title": r.title if kind == "sites" else f"Punkt {r.id}"})
        out[kind] = g
    out["antal"] = {kind: {k: len(v) for k, v in out[kind].items()} for kind in ("sites", "points")}
    out["hentet"] = {"sites": len(fetched["sites"]), "points": len(fetched["points"])}
    return out


def _short(report: dict) -> dict:
    """Rapporten til visning: højst EXAMPLES eksempler pr. gruppe (tallene står i «antal»)."""
    return {**report, **{kind: {k: v[:EXAMPLES] for k, v in report[kind].items()} for kind in ("sites", "points")}}


async def _all(db: AsyncSession) -> tuple[list[FinnSite], list[FinnPoint]]:
    return ((await db.execute(select(FinnSite))).scalars().all(), (await db.execute(select(FinnPoint))).scalars().all())


# ---------------------------------------------------------------- synkronisering


async def start_sync(db: AsyncSession, user_id: int | None) -> FinnSync:
    """Hent fra DOFbasen og lav ændringsrapporten. Ældre, ikke anvendte hentninger forkastes."""
    try:
        fetched = await finn.fetch()
    except Exception as exc:  # noqa: BLE001 – vises til admin
        raise FinnError(f"DOFbasen svarede ikke: {exc}") from exc
    if not fetched["sites"]:
        raise FinnError("DOFbasen sendte ingen FiNN-områder – intet er ændret")
    sites, points = await _all(db)
    await db.execute(text("UPDATE finn_syncs SET status = 'forkastet', data = '{}'::jsonb WHERE status = 'klar'"))
    s = FinnSync(user_id=user_id, status="klar", data=fetched, report=diff(fetched, sites, points))
    db.add(s)
    await db.commit()
    return s


async def apply_sync(db: AsyncSession, sync: FinnSync, groups: list[str], conflicts: str, user_id: int) -> dict:
    """Anvend de valgte grupper. Rapporten regnes igen mod portalens nuværende data (der kan være rettet siden)."""
    if sync.status != "klar":
        raise FinnError("Hentningen er allerede anvendt eller forkastet – hent igen")
    groups = [g for g in groups if g in GROUPS]
    sites, points = await _all(db)
    rep = diff(sync.data, sites, points)
    now = _now()
    done = {"sites": dict.fromkeys(groups, 0), "points": dict.fromkeys(groups, 0)}
    for kind, model, rows, items, to_dof, fields in (
        ("sites", FinnSite, sites, sync.data["sites"], _site_dof, SITE_FIELDS),
        ("points", FinnPoint, points, sync.data["points"], _point_dof, POINT_FIELDS),
    ):
        by_id = {r.id: r for r in rows}
        items_by = {it["id"]: it for it in items}
        for g in groups:
            for row in rep[kind][g]:
                r = by_id.get(row["id"])
                if g == "fjernede":
                    r.removed_at, r.aktiv, r.updated_at, r.updated_by = now, False, now, user_id
                    done[kind][g] += 1
                    continue
                it = items_by[row["id"]]
                new = to_dof(it)
                if r is None:
                    r = model(id=it["id"], origin="dofbasen", aktiv=True)
                    db.add(r)
                    take = True
                else:
                    take = g == "aendrede" or conflicts == "dofbasen"
                    if r.removed_at is not None:
                        r.removed_at, r.aktiv = None, True
                r.dof, r.synced_at = new, now
                if take:
                    _set(r, kind, new)
                    r.edited = False
                r.updated_at, r.updated_by = now, user_id
                done[kind][g] += 1
        # Uændrede rækker: sidst set hos DOFbasen
        for r in rows:
            if r.origin == "dofbasen" and r.id in items_by:
                r.synced_at = now
    sync.status, sync.applied, sync.applied_at, sync.data = "anvendt", done, now, {}
    await db.commit()
    await finn.changed()
    return done


def _set(r, kind: str, v: dict) -> None:
    if kind == "sites":
        r.title, r.loknr, r.ring = v["title"], v.get("loknr"), v["ring"]
    else:
        r.site_id, r.lat, r.lon, r.source, r.type = v.get("site"), v["lat"], v["lon"], v.get("source"), v.get("type")


async def discard_sync(db: AsyncSession, sync: FinnSync) -> None:
    if sync.status == "klar":
        sync.status, sync.data = "forkastet", {}
        await db.commit()


def sync_out(s: FinnSync) -> dict:
    return {"id": s.id, "status": s.status, "created_at": s.created_at.isoformat() if s.created_at else None,
            "applied_at": s.applied_at.isoformat() if s.applied_at else None, "applied": s.applied,
            "report": _short(s.report) if s.report else None}


async def syncs(db: AsyncSession, n: int = 10) -> list[FinnSync]:
    return list((await db.execute(select(FinnSync).order_by(FinnSync.id.desc()).limit(n))).scalars())


# ---------------------------------------------------------------- områder og punkter


async def sites_out(db: AsyncSession) -> dict:
    """Alle områder og punkter (også skjulte og fjernede) med antal tællinger og seneste tælling."""
    sites, points = await _all(db)
    counts = {r[0]: (r[1], r[2]) for r in (await db.execute(text(
        "SELECT finn_site_id, count(*), max(dato) FROM field_trips WHERE metode_id = 99 AND finn_site_id IS NOT NULL GROUP BY 1"))).all()}
    return {
        "sites": sorted(({**site_out(s), "taellinger": counts.get(s.id, (0, None))[0],
                          "seneste": (d.isoformat() if (d := counts.get(s.id, (0, None))[1]) else None)} for s in sites),
                        key=lambda x: x["title"].lower()),
        "points": [point_out(p) for p in sorted(points, key=lambda p: p.id)],
        "kilde": "portal" if sites else "dofbasen",
    }


def site_out(s: FinnSite) -> dict:
    return {"id": s.id, "origin": s.origin, "title": s.title, "loknr": s.loknr, "ring": s.ring, "aktiv": s.aktiv,
            "edited": s.edited, "note": s.note, "fjernet": s.removed_at.isoformat() if s.removed_at else None,
            "synced_at": s.synced_at.isoformat() if s.synced_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "dof": s.dof, "afviger": bool(s.dof) and _site_cur(s) != s.dof}


def point_out(p: FinnPoint) -> dict:
    return {"id": p.id, "origin": p.origin, "site": p.site_id, "lat": p.lat, "lon": p.lon, "source": p.source, "type": p.type,
            "aktiv": p.aktiv, "edited": p.edited, "note": p.note, "fjernet": p.removed_at.isoformat() if p.removed_at else None,
            "dof": p.dof, "afviger": bool(p.dof) and _point_cur(p) != p.dof}


def _check_ring(ring: list) -> list[list[float]]:
    if not isinstance(ring, list) or len(ring) < 3:
        raise FinnError("Grænsen skal have mindst tre hjørner")
    out = []
    for p in ring:
        if not (isinstance(p, (list, tuple)) and len(p) >= 2):
            raise FinnError("Ugyldigt hjørne i grænsen")
        lat, lon = float(p[0]), float(p[1])
        if not (54.4 <= lat <= 57.9 and 7.8 <= lon <= 15.4):
            raise FinnError("Grænsen ligger uden for Danmark")
        out.append([round(lat, 6), round(lon, 6)])
    if out[0] != out[-1]:
        out.append(out[0])  # lukket ring, som DOFbasens WKT
    if len(out) > 5000:
        raise FinnError("Grænsen har for mange hjørner")
    return out


async def _location_ok(db: AsyncSession, loknr: int | None) -> None:
    if loknr is not None and not (await db.execute(text("SELECT 1 FROM locations WHERE loknr = :l"), {"l": loknr})).scalar():
        raise FinnError("Lokaliteten findes ikke")


async def _next_id(db: AsyncSession, model) -> int:
    top = (await db.execute(select(func.max(model.id)))).scalar() or 0
    return max(top, LOCAL_ID0 - 1) + 1


async def get_site(db: AsyncSession, sid: int) -> FinnSite:
    s = await db.get(FinnSite, sid)
    if s is None:
        raise FinnError("Området findes ikke")
    return s


async def get_point(db: AsyncSession, pid: int) -> FinnPoint:
    p = await db.get(FinnPoint, pid)
    if p is None:
        raise FinnError("Punktet findes ikke")
    return p


async def create_site(db: AsyncSession, user_id: int, title: str, ring: list, loknr: int | None, note: str | None) -> FinnSite:
    await _location_ok(db, loknr)
    s = FinnSite(id=await _next_id(db, FinnSite), origin="portal", title=title.strip(), ring=_check_ring(ring), loknr=loknr,
                 note=note, aktiv=True, edited=False, updated_by=user_id, updated_at=_now())
    db.add(s)
    await db.commit()
    await finn.changed()
    return s


async def update_site(db: AsyncSession, s: FinnSite, user_id: int, changes: dict) -> FinnSite:
    if "loknr" in changes:
        await _location_ok(db, changes["loknr"])
    if "ring" in changes:
        changes["ring"] = _check_ring(changes["ring"])
    if "title" in changes:
        changes["title"] = (changes["title"] or "").strip() or s.title
    for k, v in changes.items():
        setattr(s, k, v)
    if s.origin == "dofbasen" and any(k in changes for k in SITE_FIELDS):
        s.edited = _site_cur(s) != (s.dof or {})  # rettet tilbage til DOFbasens = ikke længere rettet
    s.updated_at, s.updated_by = _now(), user_id
    await db.commit()
    await finn.changed()
    return s


async def revert_site(db: AsyncSession, s: FinnSite, user_id: int) -> FinnSite:
    """Tilbage til DOFbasens seneste version af titel, lokalitet og grænse."""
    if not s.dof:
        raise FinnError("Området har ingen version fra DOFbasen")
    _set(s, "sites", s.dof)
    s.edited, s.updated_at, s.updated_by = False, _now(), user_id
    await db.commit()
    await finn.changed()
    return s


async def delete_site(db: AsyncSession, s: FinnSite) -> None:
    """Kun portalens egne områder uden tællinger slettes; DOFbasens skjules i stedet (aktiv = false)."""
    if s.origin != "portal":
        raise FinnError("DOFbasens områder kan ikke slettes – skjul det i stedet")
    if (await db.execute(text("SELECT 1 FROM field_trips WHERE finn_site_id = :s LIMIT 1"), {"s": s.id})).scalar():
        raise FinnError("Der er tællinger i området – skjul det i stedet")
    await db.execute(text("UPDATE finn_points SET site_id = NULL WHERE site_id = :s"), {"s": s.id})
    await db.delete(s)
    await db.commit()
    await finn.changed()


def _check_point(lat: float, lon: float) -> tuple[float, float]:
    if not (54.4 <= lat <= 57.9 and 7.8 <= lon <= 15.4):
        raise FinnError("Punktet ligger uden for Danmark")
    return round(lat, 6), round(lon, 6)


async def _site_for(db: AsyncSession, lat: float, lon: float) -> int | None:
    sites = (await db.execute(select(FinnSite).where(FinnSite.removed_at.is_(None)))).scalars().all()
    return next((s.id for s in sites if finn.inside((lat, lon), s.ring or [])), None)


async def create_point(db: AsyncSession, user_id: int, lat: float, lon: float, source: str | None, type_: str,
                       note: str | None) -> FinnPoint:
    lat, lon = _check_point(lat, lon)
    p = FinnPoint(id=await _next_id(db, FinnPoint), origin="portal", lat=lat, lon=lon, source=source, type=type_, note=note,
                  site_id=await _site_for(db, lat, lon), aktiv=True, updated_by=user_id, updated_at=_now())
    db.add(p)
    await db.commit()
    await finn.changed()
    return p


async def update_point(db: AsyncSession, p: FinnPoint, user_id: int, changes: dict) -> FinnPoint:
    if "lat" in changes or "lon" in changes:
        changes["lat"], changes["lon"] = _check_point(changes.get("lat", p.lat), changes.get("lon", p.lon))
        if "site_id" not in changes:
            changes["site_id"] = await _site_for(db, changes["lat"], changes["lon"])
    for k, v in changes.items():
        setattr(p, k, v)
    if p.origin == "dofbasen":
        p.edited = _point_cur(p) != (p.dof or {})
    p.updated_at, p.updated_by = _now(), user_id
    await db.commit()
    await finn.changed()
    return p


async def revert_point(db: AsyncSession, p: FinnPoint, user_id: int) -> FinnPoint:
    if not p.dof:
        raise FinnError("Punktet har ingen version fra DOFbasen")
    _set(p, "points", p.dof)
    p.edited, p.updated_at, p.updated_by = False, _now(), user_id
    await db.commit()
    await finn.changed()
    return p


async def delete_point(db: AsyncSession, p: FinnPoint) -> None:
    if p.origin != "portal":
        raise FinnError("DOFbasens punkter kan ikke slettes – skjul det i stedet")
    await db.delete(p)
    await db.commit()
    await finn.changed()
