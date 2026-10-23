"""DOFbasen-modulet (showcase af en ny dofbasen.dk): sider, observationer, søgning, fænologi, lister og admin-panel.

Alt kræver, at funktionen "dofbasen" er synlig for brugeren (Admin → Funktioner). Redigering kræver en DOFbasen-rolle
med den relevante rettighed (se services.dofbasen.PERMS). Det offentlige token-API ligger i dofbasen_v1.py.
"""

from __future__ import annotations

import csv
import io
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import cached
from portal.db import get_session
from portal.models import DofbApiGrant, DofbApiToken, DofbArea, DofbFocus, DofbList, DofbNews, DofbObsStatus, DofbPage, DofbRole, User
from portal.services import dofbasen as svc
from portal.services import features
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/dofbasen", tags=["dofbasen"])
FORSIDE_TTL = 300  # forsidens kort og grafer opdateres hvert 5. minut (som på dofbasen.dk)


def need(feature: str = "dofbasen", perm: str | None = None, edit: bool = False):
    """Afhængighed: funktionen skal være synlig for brugeren (ellers 404), og evt. kræve en DOFbasen-rettighed."""

    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> svc.Ctx:
        ctx = await svc.context(db, user)
        if not ctx.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if perm and not (ctx.can_edit(perm) if edit else ctx.can(perm)):
            label = svc.PERMS[perm].split(":")[0]
            raise HTTPException(403, "Kræver DOFbasen-rettigheden " + label[0].lower() + label[1:])
        return ctx

    return dep


async def _row(db: AsyncSession, sql: str, **args) -> dict | None:
    r = (await db.execute(text(sql), args)).mappings().first()
    return dict(r) if r else None


async def _rows(db: AsyncSession, sql: str, **args) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql), args)).mappings().all()]


# ---------------------------------------------------------------- fælles


@router.get("/ctx")
async def ctx_info(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    """Hvad brugeren ser og må i modulet + menuens eksterne links. Svarer også når modulet er slået fra (enabled=false)."""
    ctx = await svc.context(db, user)
    out = {"enabled": ctx.on("dofbasen"), **ctx.out(), "superadmin": ctx.superadmin}
    if not out["enabled"]:
        return out
    lists = {r.key: r.rows for r in (await db.execute(select(DofbList).where(DofbList.key.in_(["links", "stoet", "statistik"])))).scalars()}
    out["menu"] = lists
    out["afdelinger"] = await _rows(db, "SELECT id, navn FROM afdelinger ORDER BY id")
    out["api_access"] = bool(user and await db.get(DofbApiGrant, user.id))
    return out


@router.get("/counts")
async def counts(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    async def produce():
        return await _row(db, """SELECT (SELECT count(*) FROM locations) AS lokaliteter,
                                        (SELECT reltuples::bigint FROM pg_class WHERE relname = 'observations') AS observationer,
                                        (SELECT count(*) FROM observers) AS observatorer""")
    return await cached("dofb:counts", 3600, produce)


# ---------------------------------------------------------------- forside


@router.get("/forside/kort")
async def forside_kort(dag: str = Query("idag", pattern="^(idag|igaar)$"), ctx: svc.Ctx = Depends(need()),
                       db: AsyncSession = Depends(get_session)):
    """Lokaliteter med fugleobservationer i dag/i går (kortet øverst på forsiden)."""
    d = dk_today() - timedelta(days=dag == "igaar")

    async def produce():
        rows = await _rows(db, """
            SELECT l.loknr, l.navn, l.lat, l.lon, count(*) AS n, count(DISTINCT o.artnr) AS arter,
                   bool_or(o.kategori = 'SU') AS su, bool_or(o.kategori = 'SUB') AS sub
            FROM observations o JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato = :d AND o.hemmelig = 0 AND l.lat IS NOT NULL GROUP BY l.loknr
        """, d=d)
        return {"dato": d.isoformat(), "updated": datetime.now(timezone.utc).isoformat(), "points": rows}
    return await cached(f"dofb:kort:{d}", FORSIDE_TTL, produce)


@router.get("/forside/antal")
async def forside_antal(dage: int = Query(20, ge=7, le=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Antal observationer pr. dag de seneste N dage."""
    today = dk_today()

    async def produce():
        rows = await _rows(db, "SELECT dato, count(*) AS n FROM observations WHERE dato > :d GROUP BY dato ORDER BY dato",
                           d=today - timedelta(days=dage))
        by = {r["dato"]: r["n"] for r in rows}
        return [{"dato": (today - timedelta(days=i)).isoformat(), "n": by.get(today - timedelta(days=i), 0)} for i in range(dage - 1, -1, -1)]
    return await cached(f"dofb:antal:{today}:{dage}", FORSIDE_TTL, produce)


@router.get("/forside/afdelinger")
async def forside_afdelinger(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Fordelingen af observationer lokalafdelingerne imellem (indeværende år)."""
    today = dk_today()

    async def produce():
        return await _rows(db, """
            SELECT a.id, a.navn, count(o.*) AS n FROM afdelinger a
            LEFT JOIN locations l ON l.afdeling_id = a.id
            LEFT JOIN observations o ON o.loknr = l.loknr AND o.dato >= :d
            GROUP BY a.id ORDER BY a.id
        """, d=date(today.year, 1, 1))
    return await cached(f"dofb:afdfordeling:{today}", 6 * 3600, produce)


@router.get("/forside/sjaeldne")
async def forside_sjaeldne(sjf: str = Query("nu", pattern="^(nu|dato)$"), ctx: svc.Ctx = Depends(need()),
                           db: AsyncSession = Depends(get_session)):
    """Sjældne fugle (SU): seneste observationer, eller samme dato i tidligere år."""
    today = dk_today()

    async def produce():
        if sjf == "nu":
            return await _rows(db, """
                SELECT DISTINCT ON (o.dato, o.artnr, o.loknr) o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, o.obsid
                FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
                WHERE o.dato > :d AND o.kategori = 'SU' AND o.hemmelig = 0
                ORDER BY o.dato DESC, o.artnr, o.loknr, o.obsid LIMIT 12
            """, d=today - timedelta(days=21))
        dates = []
        for y in range(today.year - 1, 1990, -1):
            try:
                dates.append(date(y, today.month, today.day))
            except ValueError:
                pass
        return await _rows(db, """
            SELECT DISTINCT ON (o.dato, o.artnr, o.loknr) o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, o.obsid
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato = ANY(:dates) AND o.kategori = 'SU' AND o.hemmelig = 0
            ORDER BY o.dato DESC, o.artnr, o.loknr, o.obsid LIMIT 12
        """, dates=dates)
    return await cached(f"dofb:sjf:{sjf}:{today}", FORSIDE_TTL if sjf == "nu" else 3600, produce)


@router.get("/forside/statistik")
async def forside_statistik(stat: str = Query("stat", pattern="^(stat|faeno|afvig)$"), ctx: svc.Ctx = Depends(need()),
                            db: AsyncSession = Depends(get_session)):
    """Forsidens statistik-boks: flest ture i år, seneste førstegangsfund i år, eller dagens fænologiske afvigere."""
    today = dk_today()

    async def produce():
        if stat == "stat":
            return await _rows(db, """
                SELECT t.obserkode, trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS navn, count(*) AS n
                FROM trips t LEFT JOIN observers ob ON ob.obserkode = t.obserkode
                WHERE t.dato >= :d GROUP BY t.obserkode, ob.fornavn, ob.efternavn ORDER BY n DESC LIMIT 5
            """, d=date(today.year, 1, 1))
        if stat == "faeno":
            return (await firstobs_data(db, today.year))[:5]
        return await _rows(db, """
            SELECT DISTINCT ON (o.artnr) o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, o.antal, o.obsid
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato >= :d AND o.kategori = 'bemaerk' AND o.hemmelig = 0
              AND EXISTS (SELECT 1 FROM phenology_windows pw WHERE pw.artnavn = s.navn)
            ORDER BY o.artnr, o.dato DESC LIMIT 8
        """, d=today - timedelta(days=2))
    return await cached(f"dofb:stat:{stat}:{today}", 3600 if stat == "stat" else FORSIDE_TTL, produce)


@router.get("/forside/art")
async def forside_art(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Danmarks fugle: dagens art med forekomst pr. måned de seneste 10 hele år."""
    today = dk_today()

    async def produce():
        ids = [r[0] for r in (await db.execute(text("""
            SELECT artnr FROM species WHERE art_type = 'art' AND klass = 'Alm' AND navn NOT LIKE '[%' ORDER BY artnr
        """))).all()]
        if not ids:
            return None
        artnr = random.Random(today.toordinal()).choice(ids)
        return await species_profile(db, artnr)
    return await cached(f"dofb:dagensart:{today}", 6 * 3600, produce)


@router.get("/forside/fotos")
async def forside_fotos(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Seneste fotos uploadet til observationer (portalens egne indtastninger – DOFbasens fotos hentes ikke)."""
    return await _rows(db, """
        SELECT r.id, r.dato, s.navn AS art, l.navn AS lokalitet, m->>'url' AS url
        FROM field_reports r CROSS JOIN LATERAL jsonb_array_elements(r.media) m
        JOIN species s ON s.artnr = r.artnr LEFT JOIN locations l ON l.loknr = r.loknr
        WHERE NOT r.hemmelig AND coalesce(m->>'kind', 'image') = 'image' ORDER BY r.created_at DESC LIMIT 15
    """)


async def species_profile(db: AsyncSession, artnr: int) -> dict | None:
    """Artens forekomst: andel af ture med arten pr. 10-dagesperiode de seneste 10 hele år (DOFbasens farvekoder) og i år."""
    sp = await _row(db, "SELECT artnr, navn, latin, klass FROM species WHERE artnr = :a", a=artnr)
    if sp is None:
        return None
    today = dk_today()
    y1, y2 = today.year - 10, today.year - 1
    rows = await _rows(db, """
        WITH p AS (
            SELECT CASE WHEN extract(year FROM e.dato) = :y THEN 'aar' ELSE 'ref' END AS serie,
                   least(35, (extract(month FROM e.dato)::int - 1) * 3 + least(2, (extract(day FROM e.dato)::int - 1) / 10)) AS periode,
                   e.n_trips, coalesce(sd.n_trips, 0) AS art_trips, coalesce(sd.n_ind, 0) AS ind
            FROM effort_day e LEFT JOIN species_day sd ON sd.dato = e.dato AND sd.artnr = :a
            WHERE e.dato BETWEEN :d1 AND :d2 OR extract(year FROM e.dato) = :y
        )
        SELECT serie, periode, sum(n_trips) AS ture, sum(art_trips) AS art_ture, sum(ind) AS ind FROM p GROUP BY 1, 2
    """, a=artnr, y=today.year, d1=date(y1, 1, 1), d2=date(y2, 12, 31))
    ref = [{"ture": 0, "art_ture": 0, "ind": 0} for _ in range(36)]
    aar = [{"ture": 0, "art_ture": 0, "ind": 0} for _ in range(36)]
    for r in rows:
        (aar if r["serie"] == "aar" else ref)[int(r["periode"])] = {k: int(r[k] or 0) for k in ("ture", "art_ture", "ind")}

    def cat(x: dict) -> int:
        # 0 tilfældig … 6 meget almindelig (andel af ture med arten)
        if not x["ture"] or not x["art_ture"]:
            return 0
        rate = x["art_ture"] / x["ture"]
        return next(i for i, lim in enumerate((0.0005, 0.002, 0.01, 0.04, 0.12, 0.3, 2), start=0) if rate < lim)

    return {**sp, "years": [y1, y2], "periods": [{"ref_rate": (r["art_ture"] / r["ture"]) if r["ture"] else 0,
                                                    "ref_per_tur": (r["ind"] / r["ture"]) if r["ture"] else 0,
                                                    "aar_per_tur": (a["ind"] / a["ture"]) if a["ture"] else None,
                                                    "cat": cat(r)} for r, a in zip(ref, aar)]}


# ---------------------------------------------------------------- observationer (dagens liste)

VISNING = {"fugle": "o.kategori <> 'alm'", "allfugle": "TRUE", "sufugle": "o.kategori IN ('SU', 'SUB')",
           "trkfugle": svc.ADF[2][1]}


@router.get("/observationer")
async def observationer(dato: date | None = None, visning: str = Query("fugle", pattern="^(fugle|allfugle|sufugle|trkfugle|patdyr|somfugle|krybdyr)$"),
                        lok: int | None = None, lokalitet: int | None = None, summering: str = Query("tur", pattern="^(tur|dato|ingen)$"),
                        user: User = Depends(require_user), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Observationer på en dag – som /observationer/index.php: visning, afdeling og summering (tur/dato/ingen)."""
    d = dato or dk_today()
    if visning in ("patdyr", "somfugle", "krybdyr"):
        return {"dato": d.isoformat(), "rows": [], "note": "Portalen henter kun fugle fra DOFbasen – andre dyr findes ikke i data."}
    where = [VISNING[visning], "o.dato = :d", "(o.hemmelig = 0 OR o.obserkode = :me)"]
    args: dict[str, Any] = {"d": d, "me": user.obserkode}
    if lok:
        where.append("l.afdeling_id = :lok")
        args["lok"] = lok
    if lokalitet:
        where.append("o.loknr = :loknr")
        args["loknr"] = lokalitet
    group = {"tur": "o.turid, o.artnr", "dato": "o.obserkode, o.loknr, o.artnr", "ingen": "o.obsid"}[summering]
    rows = await _rows(db, f"""
        SELECT min(o.obsid) AS obsid, count(*) AS n_obs, sum(o.antal) AS antal, o.artnr, s.navn AS art, s.latin, s.sortering,
               min(o.kategori) FILTER (WHERE o.kategori <> 'alm') AS kat, o.loknr, l.navn AS lokalitet, l.afdeling_id,
               o.obserkode, max(trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, ''))) AS observator,
               string_agg(DISTINCT o.adfkode, ', ') AS adfkode, string_agg(DISTINCT o.alderkode, ', ') AS alder,
               string_agg(DISTINCT o.koen, ', ') AS koen, string_agg(DISTINCT o.dragtkode, ', ') AS dragt,
               bool_or(coalesce(o.fuglnoter, '') <> '') AS kommentar, bool_or(coalesce(t.turnoter, '') <> '') AS turkommentar,
               bool_or(coalesce(t.medobser, '') <> '') AS medobser, bool_or(o.obs_lat IS NOT NULL) AS position,
               bool_or(o.obstidfra IS NOT NULL OR t.turtidfra IS NOT NULL) AS tid, max(st.status) AS dku
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN trips t ON t.turid = o.turid LEFT JOIN observers ob ON ob.obserkode = o.obserkode
        LEFT JOIN dofb_obs_status st ON st.obsid = o.obsid
        WHERE {' AND '.join(where)}
        GROUP BY {group}, s.navn, s.latin, s.sortering, o.loknr, l.navn, l.afdeling_id, o.obserkode, o.artnr
        ORDER BY l.afdeling_id, s.sortering, l.navn LIMIT 10000
    """, **args)
    return {"dato": d.isoformat(), "rows": rows, "summering": summering}


@router.get("/obs/{obsid}")
async def observation(obsid: int, user: User = Depends(require_user), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Én observation (popobs.php) og alle observationer på samme tur."""
    o = await _row(db, f"""SELECT {svc.ROW_COLS}, o.first_seen, st.note AS dku_note, st.set_at AS dku_at
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN trips t ON t.turid = o.turid {svc.ROW_JOINS}
        WHERE o.obsid = :id AND (o.hemmelig = 0 OR o.obserkode = :me)""", id=obsid, me=user.obserkode, sub_as_species=False)
    if o is None:
        raise HTTPException(404, "Observationen findes ikke")
    tur = await _rows(db, """
        SELECT o.obsid, o.artnr, s.navn AS art, o.antal, o.adfkode, ct.text AS adfaerd, o.kategori
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN code_texts ct ON ct.kind = 'adf' AND ct.code = o.adfkode
        WHERE o.turid = :t AND (o.hemmelig = 0 OR o.obserkode = :me) ORDER BY s.sortering, o.obsid
    """, t=o["turid"], me=user.obserkode) if o["turid"] else []
    o["can_dku"] = ctx.can("dku") and ctx.on("dofbasen_dku")
    return {"obs": o, "tur": tur}


class DkuIn(BaseModel):
    status: str = Field(pattern="^(|behandling|dku|su)$")
    note: str = Field("", max_length=2000)


@router.put("/obs/{obsid}/dku")
async def set_dku(obsid: int, body: DkuIn, ctx: svc.Ctx = Depends(need("dofbasen_dku", "dku")), db: AsyncSession = Depends(get_session)):
    row = await db.get(DofbObsStatus, obsid)
    if not body.status:
        if row:
            await db.delete(row)
    elif row is None:
        db.add(DofbObsStatus(obsid=obsid, status=body.status, note=body.note or None, set_by=ctx.user.id))
    else:
        row.status, row.note, row.set_by, row.set_at = body.status, body.note or None, ctx.user.id, datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "dku", f"obs {obsid}: {body.status or 'fjernet'}")
    await db.commit()
    return {"ok": True}


@router.get("/lok/{loknr}")
async def lokalitet(loknr: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Lokalitet (poplok.php): stamdata, tal og seneste observationer."""
    loc = await _row(db, """
        SELECT l.loknr, l.navn, l.lat, l.lon, l.afdeling_id, a.navn AS afdeling, l.kommune_kode, k.navn AS kommune,
               (SELECT array_agg(json_build_object('id', id, 'kind', kind, 'navn', navn)) FROM dofb_areas WHERE l.loknr = ANY(loknrs)) AS omraader
        FROM locations l LEFT JOIN afdelinger a ON a.id = l.afdeling_id LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.loknr = :l""", l=loknr)
    if loc is None:
        raise HTTPException(404, "Lokaliteten findes ikke")

    async def produce():
        return await _row(db, """
            SELECT count(*) AS n_obs, count(DISTINCT o.artnr) FILTER (WHERE s.art_type = 'art') AS n_arter,
                   min(o.dato) AS first, max(o.dato) AS last, count(DISTINCT o.obserkode) AS n_obser
            FROM observations o JOIN species s ON s.artnr = o.artnr WHERE o.loknr = :l AND o.hemmelig = 0""", l=loknr)
    loc["stats"] = await cached(f"dofb:lokstat:{loknr}:{dk_today()}", 6 * 3600, produce)
    loc["seneste"] = await _rows(db, """
        SELECT o.obsid, o.dato, s.navn AS art, o.antal, o.kategori FROM observations o JOIN species s ON s.artnr = o.artnr
        WHERE o.loknr = :l AND o.hemmelig = 0 ORDER BY o.dato DESC, s.sortering LIMIT 25""", l=loknr)
    return loc


@router.get("/obser/{kode}")
async def observator(kode: str, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Observatør (popobser.php) – uden adresse og telefon. Tal pr. måned og år."""
    kode = kode.upper()[:16]
    ob = await _row(db, "SELECT obserkode, fornavn, efternavn, by FROM observers WHERE obserkode = :k", k=kode)
    if ob is None:
        raise HTTPException(404, "Observatøren findes ikke")

    async def produce():
        stats = await _row(db, """
            SELECT count(*) AS n_obs, max(o.dato) AS last, count(DISTINCT coalesce(s.list_artnr, o.artnr)) FILTER (WHERE s.list_artnr IS NOT NULL) AS n_arter
            FROM observations o JOIN species s ON s.artnr = o.artnr WHERE o.obserkode = :k""", k=kode)
        months = await _rows(db, "SELECT extract(month FROM dato)::int AS m, count(*) AS n FROM observations WHERE obserkode = :k GROUP BY 1 ORDER BY 1", k=kode)
        years = await _rows(db, "SELECT extract(year FROM dato)::int AS y, count(*) AS n FROM observations WHERE obserkode = :k GROUP BY 1 ORDER BY 1", k=kode)
        return {"stats": stats, "months": months, "years": years}
    return {**ob, **await cached(f"dofb:obser:{kode}:{dk_today()}", 6 * 3600, produce)}


# ---------------------------------------------------------------- søgning


@router.post("/soeg")
async def soeg(body: svc.SearchIn, user: User = Depends(require_user), ctx: svc.Ctx = Depends(need("dofbasen_search")),
               db: AsyncSession = Depends(get_session)):
    try:
        return await svc.search(db, body, user.obserkode)
    except Exception as exc:  # noqa: BLE001 – typisk statement_timeout
        if "statement timeout" in str(exc) or "canceling statement" in str(exc):
            raise HTTPException(422, "Søgningen tog for lang tid. Indsnævr periode, område eller art.") from exc
        raise


@router.post("/soeg/csv")
async def soeg_csv(body: svc.SearchIn, user: User = Depends(require_user), ctx: svc.Ctx = Depends(need("dofbasen_search")),
                   db: AsyncSession = Depends(get_session)):
    """Søgeresultatet som semikolonsepareret fil (DOFbasens kolonnenavne, UTF-8 med BOM), højst 50.000 rækker."""
    res = await svc.search(db, body, user.obserkode, csv=True)
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(svc.CSV_COLUMNS)
    for r in res["rows"]:
        w.writerow(["" if v is None else v for v in svc.csv_line(r)])
    name = f"dofbasen-soegning-{dk_today().isoformat()}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/soeg/valg")
async def soeg_valg(ctx: svc.Ctx = Depends(need("dofbasen_search")), db: AsyncSession = Depends(get_session)):
    """Valgmuligheder til søgeformularen."""
    return {
        "species": svc.SPECIES_GROUPS, "adf": {k: v[0] for k, v in svc.ADF.items()}, "alder": svc.ALDER, "dragt": svc.DRAGT,
        "metode": svc.METODER, "kommuner": await _rows(db, "SELECT kode, navn FROM kommuner ORDER BY navn"),
        "omraader": await _rows(db, "SELECT id, kind, navn FROM dofb_areas ORDER BY kind, navn"),
    }


@router.get("/arter/soeg")
async def arter_soeg(q: str = Query(min_length=2, max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, """
        SELECT artnr, navn, latin, art_type FROM species WHERE navn ILIKE :q OR latin ILIKE :q
        ORDER BY (navn ILIKE :p) DESC, art_type <> 'art', sortering LIMIT 20""", q=f"%{q}%", p=f"{q}%")


@router.get("/lokaliteter/soeg")
async def lok_soeg(q: str = Query(min_length=2, max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, """
        SELECT l.loknr, l.navn, a.navn AS afdeling, k.navn AS kommune FROM locations l LEFT JOIN afdelinger a ON a.id = l.afdeling_id
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode WHERE l.navn ILIKE :q ORDER BY (l.navn ILIKE :p) DESC, length(l.navn) LIMIT 20
    """, q=f"%{q}%", p=f"{q}%")


# ---------------------------------------------------------------- fænologi


async def firstobs_data(db: AsyncSession, year: int) -> list[dict]:
    rows = await _rows(db, """
        SELECT DISTINCT ON (o.artnr) o.artnr, s.navn AS art, s.klass, o.dato, o.loknr, l.navn AS lokalitet, o.obsid,
               count(*) OVER (PARTITION BY o.artnr) AS n
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE o.dato BETWEEN :a AND :b AND o.hemmelig = 0 AND s.art_type IN ('art', 'underart')
        ORDER BY o.artnr, o.dato, o.obsid
    """, a=date(year, 1, 1), b=date(year, 12, 31))
    rows.sort(key=lambda r: (r["dato"], r["art"]), reverse=True)
    for r in rows:
        r["dato"] = r["dato"].isoformat()
    return rows


@router.get("/faeno/firstobs")
async def firstobs(aar: int | None = None, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Førstegangs-iagttagelser i året (nyeste først) med antal observationer hele året."""
    y = aar or dk_today().year

    async def produce():
        return await firstobs_data(db, y)
    return {"aar": y, "rows": await cached(f"dofb:firstobs:{y}:{dk_today()}", 3 * 3600, produce)}


@router.get("/faeno/samedate")
async def samedate(dato: date | None = None, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Sjældne, fåtallige og periodemæssigt usædvanlige fugle på samme dato i tidligere år."""
    d = dato or dk_today()
    dates = []
    for y in range(d.year - 1, 1969, -1):
        try:
            dates.append(date(y, d.month, d.day))
        except ValueError:
            pass

    async def produce():
        rows = await _rows(db, """
            SELECT extract(year FROM o.dato)::int AS aar, o.artnr, s.navn AS art, min(o.kategori) AS kat, count(*) AS n
            FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE o.dato = ANY(:dates) AND o.kategori IN ('SU', 'SUB', 'bemaerk') AND o.hemmelig = 0
              AND (o.kategori <> 'bemaerk' OR EXISTS (SELECT 1 FROM phenology_windows pw WHERE pw.artnavn = s.navn))
            GROUP BY 1, o.artnr, s.navn, s.sortering ORDER BY 1 DESC, s.sortering
        """, dates=dates)
        return rows
    return {"dato": d.isoformat(), "rows": await cached(f"dofb:samedate:{d.month}-{d.day}:{dk_today()}", 6 * 3600, produce)}


@router.get("/faeno/art/{artnr}")
async def faeno_art(artnr: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artens fænologi: første iagttagelse de seneste 10 år (højst 5 lokaliteter) og forekomst pr. 10-dagesperiode."""
    today = dk_today()

    async def produce():
        prof = await species_profile(db, artnr)
        if prof is None:
            return None
        first = []
        for y in range(today.year, today.year - 10, -1):
            r = await _rows(db, """
                WITH f AS (SELECT min(dato) AS d FROM observations WHERE artnr = :a AND dato BETWEEN :y1 AND :y2 AND hemmelig = 0)
                SELECT f.d AS dato, l.loknr, l.navn FROM f JOIN observations o ON o.artnr = :a AND o.dato = f.d AND o.hemmelig = 0
                LEFT JOIN locations l ON l.loknr = o.loknr GROUP BY f.d, l.loknr, l.navn ORDER BY l.navn LIMIT 5
            """, a=artnr, y1=date(y, 1, 1), y2=date(y, 12, 31))
            if r:
                first.append({"aar": y, "dato": r[0]["dato"].isoformat(), "lokaliteter": [{"loknr": x["loknr"], "navn": x["navn"]} for x in r]})
        prof["first"] = first
        prof["seneste14"] = (await _row(db, "SELECT count(*) AS n FROM observations WHERE artnr = :a AND dato > :d AND hemmelig = 0",
                                        a=artnr, d=today - timedelta(days=14)))["n"]
        return prof
    data = await cached(f"dofb:faenoart:{artnr}:{today}", 6 * 3600, produce)
    if data is None:
        raise HTTPException(404, "Arten findes ikke")
    return data


@router.get("/faeno/artkort")
async def artkort(art: int, aar: int | None = None, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artsforekomst på kort: lokaliteter med arten i året (antal observationer og største antal)."""
    y = aar or dk_today().year

    async def produce():
        return await _rows(db, """
            SELECT l.loknr, l.navn, l.lat, l.lon, count(*) AS n, max(o.antal) AS max FROM observations o JOIN locations l ON l.loknr = o.loknr
            WHERE o.artnr = :a AND o.dato BETWEEN :d1 AND :d2 AND o.hemmelig = 0 AND l.lat IS NOT NULL GROUP BY l.loknr
        """, a=art, d1=date(y, 1, 1), d2=date(y, 12, 31))
    return {"aar": y, "points": await cached(f"dofb:artkort:{art}:{y}:{dk_today()}", 3600, produce)}


# ---------------------------------------------------------------- opslagslister (levende fra databasen)


@router.get("/opslag/arter")
async def opslag_arter(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, "SELECT artnr, navn, latin, art_type, klass FROM species WHERE NOT excluded ORDER BY sortering, artnr")


@router.get("/opslag/observatorer")
async def opslag_observatorer(q: str = Query("", max_length=60), page: int = Query(1, ge=1), ctx: svc.Ctx = Depends(need()),
                              db: AsyncSession = Depends(get_session)):
    """Observatørliste: obserkode, navn og by (ingen kontaktoplysninger). 200 pr. side."""
    where, args = "TRUE", {"off": (page - 1) * 200}
    if q.strip():
        where = "(obserkode ILIKE :q OR fornavn || ' ' || efternavn ILIKE :q OR by ILIKE :q)"
        args["q"] = f"%{q.strip()}%"
    total = (await db.execute(text(f"SELECT count(*) FROM observers WHERE {where}"), args)).scalar()
    rows = await _rows(db, f"SELECT obserkode, fornavn, efternavn, by FROM observers WHERE {where} ORDER BY efternavn, fornavn LIMIT 200 OFFSET :off", **args)
    return {"total": total, "rows": rows}


@router.get("/opslag/lokaliteter")
async def opslag_lokaliteter(afdeling: int = Query(...), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, """
        SELECT l.loknr, l.navn, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.afdeling_id = :a ORDER BY l.navn""", a=afdeling)


@router.get("/opslag/kort")
async def opslag_kort(s: float, w: float, n: float, e: float, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Lokaliteter i kortudsnittet (højst 1.500; zoom ind for at se alle)."""
    rows = await _rows(db, """
        SELECT loknr, navn, lat, lon FROM locations WHERE lat BETWEEN :s AND :n AND lon BETWEEN :w AND :e LIMIT 1501
    """, s=s, w=w, n=n, e=e)
    return {"points": rows[:1500], "more": len(rows) > 1500}


@router.get("/opslag/bemaerk")
async def opslag_bemaerk(afdeling: int = Query(574), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Antalsregler for bemærkelsesværdige observationer pr. lokalafdeling."""
    return await _rows(db, """
        SELECT b.artnavn, b.antal FROM bemaerk_thresholds b LEFT JOIN species s ON s.navn = b.artnavn
        WHERE b.afdeling_id = :a ORDER BY s.sortering NULLS LAST, b.artnavn""", a=afdeling)


@router.get("/omraader")
async def omraader(kind: str = Query(pattern="^(stor|iba)$"), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, "SELECT id, dof_id, navn, cardinality(loknrs) AS n FROM dofb_areas WHERE kind = :k ORDER BY navn", k=kind)


@router.get("/omraader/{area_id}")
async def omraade(area_id: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    a = await db.get(DofbArea, area_id)
    if a is None:
        raise HTTPException(404)
    locs = await _rows(db, "SELECT loknr, navn FROM locations WHERE loknr = ANY(:l) ORDER BY navn", l=a.loknrs or [])
    known = {x["loknr"] for x in locs}
    locs += [{"loknr": n, "navn": None} for n in a.loknrs or [] if n not in known]
    return {"id": a.id, "kind": a.kind, "dof_id": a.dof_id, "navn": a.navn, "lokaliteter": locs}


# ---------------------------------------------------------------- redigerbart indhold: sider, lister, nyheder, fokus


class Section(BaseModel):
    title: str = Field("", max_length=200)
    body: str = Field("", max_length=60_000)


class PageIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    sections: list[Section] = Field(max_length=80)


def _page_out(p: DofbPage) -> dict:
    return {"slug": p.slug, "title": p.title, "sections": p.sections, "updated_at": p.updated_at}


@router.get("/sider")
async def sider(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return [{"slug": p.slug, "title": p.title, "updated_at": p.updated_at} for p in (await db.execute(select(DofbPage).order_by(DofbPage.title))).scalars()]


@router.get("/sider/{slug}")
async def side(slug: str, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    p = await db.get(DofbPage, slug)
    if p is None:
        raise HTTPException(404, "Siden findes ikke")
    return {**_page_out(p), "can_edit": ctx.can_edit("sider")}


@router.put("/sider/{slug}")
async def gem_side(slug: str, body: PageIn, ctx: svc.Ctx = Depends(need(perm="sider")), db: AsyncSession = Depends(get_session)):
    p = await db.get(DofbPage, slug)
    if p is None:
        if not slug.replace("_", "").isalnum() or len(slug) > 40:
            raise HTTPException(400, "Ugyldig adresse")
        p = DofbPage(slug=slug)
        db.add(p)
    p.title, p.sections = body.title, [s.model_dump() for s in body.sections]
    p.updated_by, p.updated_at = ctx.user.id, datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "side", slug)
    await db.commit()
    return _page_out(p)


@router.delete("/sider/{slug}")
async def slet_side(slug: str, ctx: svc.Ctx = Depends(need(perm="sider")), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(DofbPage).where(DofbPage.slug == slug))
    await svc.audit(db, ctx.user, "slet side", slug)
    await db.commit()
    return {"ok": True}


class ListIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    intro: str = Field("", max_length=4000)
    columns: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[str]] = Field(max_length=5000)


@router.get("/lister")
async def lister(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return [{"key": x.key, "title": x.title, "n": len(x.rows or []), "updated_at": x.updated_at}
            for x in (await db.execute(select(DofbList).order_by(DofbList.title))).scalars()]


@router.get("/lister/{key}")
async def liste(key: str, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    x = await db.get(DofbList, key)
    if x is None:
        raise HTTPException(404, "Listen findes ikke")
    return {"key": x.key, "title": x.title, "intro": x.intro, "columns": x.columns, "rows": x.rows, "updated_at": x.updated_at,
            "can_edit": ctx.can_edit("lister")}


@router.put("/lister/{key}")
async def gem_liste(key: str, body: ListIn, ctx: svc.Ctx = Depends(need(perm="lister")), db: AsyncSession = Depends(get_session)):
    x = await db.get(DofbList, key)
    if x is None:
        if not key.replace("_", "").isalnum() or len(key) > 40:
            raise HTTPException(400, "Ugyldig nøgle")
        x = DofbList(key=key)
        db.add(x)
    n = len(body.columns)
    x.title, x.intro, x.columns = body.title, body.intro, body.columns
    x.rows = [[str(c)[:2000] for c in (r + [""] * n)[:n]] for r in body.rows]
    x.updated_by, x.updated_at = ctx.user.id, datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "liste", key)
    await db.commit()
    return {"ok": True}


class AreaIn(BaseModel):
    kind: str = Field(pattern="^(stor|iba)$")
    navn: str = Field(min_length=1, max_length=200)
    loknrs: list[int] = Field(max_length=5000)


@router.post("/omraader")
async def nyt_omraade(body: AreaIn, ctx: svc.Ctx = Depends(need(perm="lister")), db: AsyncSession = Depends(get_session)):
    a = DofbArea(kind=body.kind, navn=body.navn, loknrs=sorted(set(body.loknrs)))
    db.add(a)
    await svc.audit(db, ctx.user, "nyt område", body.navn)
    await db.commit()
    return {"id": a.id}


@router.put("/omraader/{area_id}")
async def gem_omraade(area_id: int, body: AreaIn, ctx: svc.Ctx = Depends(need(perm="lister")), db: AsyncSession = Depends(get_session)):
    a = await db.get(DofbArea, area_id)
    if a is None:
        raise HTTPException(404)
    a.kind, a.navn, a.loknrs = body.kind, body.navn, sorted(set(body.loknrs))
    await svc.audit(db, ctx.user, "område", body.navn)
    await db.commit()
    return {"ok": True}


@router.delete("/omraader/{area_id}")
async def slet_omraade(area_id: int, ctx: svc.Ctx = Depends(need(perm="lister")), db: AsyncSession = Depends(get_session)):
    a = await db.get(DofbArea, area_id)
    if a:
        await svc.audit(db, ctx.user, "slet område", a.navn)
        await db.delete(a)
        await db.commit()
    return {"ok": True}


class NewsIn(BaseModel):
    dato: date
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=20_000)
    published: bool = True
    pinned: bool = False


def _news_out(n: DofbNews) -> dict:
    return {"id": n.id, "dato": n.dato.isoformat(), "title": n.title, "body": n.body, "published": n.published, "pinned": n.pinned,
            "updated_at": n.updated_at}


@router.get("/nyheder")
async def nyheder(alle: bool = False, ctx: svc.Ctx = Depends(need("dofbasen_news")), db: AsyncSession = Depends(get_session)):
    q = select(DofbNews).order_by(DofbNews.dato.desc(), DofbNews.id.desc())
    if not (alle and ctx.can("nyheder")):
        q = q.where(DofbNews.published)
    return {"rows": [_news_out(n) for n in (await db.execute(q)).scalars()], "can_edit": ctx.can_edit("nyheder")}


@router.get("/nyheder/{nid}")
async def nyhed(nid: int, ctx: svc.Ctx = Depends(need("dofbasen_news")), db: AsyncSession = Depends(get_session)):
    n = await db.get(DofbNews, nid)
    if n is None or (not n.published and not ctx.can("nyheder")):
        raise HTTPException(404, "Nyheden findes ikke")
    return {**_news_out(n), "can_edit": ctx.can_edit("nyheder")}


@router.post("/nyheder")
async def ny_nyhed(body: NewsIn, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    n = DofbNews(**body.model_dump(), author_id=ctx.user.id)
    db.add(n)
    await svc.audit(db, ctx.user, "ny nyhed", body.title)
    await db.commit()
    return _news_out(n)


@router.put("/nyheder/{nid}")
async def gem_nyhed(nid: int, body: NewsIn, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    n = await db.get(DofbNews, nid)
    if n is None:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(n, k, v)
    n.updated_at = datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "nyhed", body.title)
    await db.commit()
    return _news_out(n)


@router.delete("/nyheder/{nid}")
async def slet_nyhed(nid: int, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    n = await db.get(DofbNews, nid)
    if n:
        await svc.audit(db, ctx.user, "slet nyhed", n.title)
        await db.delete(n)
        await db.commit()
    return {"ok": True}


class FocusIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(max_length=20_000)
    active: bool = True
    sort: int = 100


def _focus_out(f: DofbFocus) -> dict:
    return {"id": f.id, "title": f.title, "body": f.body, "active": f.active, "sort": f.sort}


@router.get("/fokus")
async def fokus(alle: bool = False, ctx: svc.Ctx = Depends(need("dofbasen_news")), db: AsyncSession = Depends(get_session)):
    q = select(DofbFocus).order_by(DofbFocus.sort, DofbFocus.id)
    if not (alle and ctx.can("nyheder")):
        q = q.where(DofbFocus.active)
    return {"rows": [_focus_out(f) for f in (await db.execute(q)).scalars()], "can_edit": ctx.can_edit("nyheder")}


@router.post("/fokus")
async def nyt_fokus(body: FocusIn, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    f = DofbFocus(**body.model_dump())
    db.add(f)
    await svc.audit(db, ctx.user, "nyt fokus", body.title)
    await db.commit()
    return _focus_out(f)


@router.put("/fokus/{fid}")
async def gem_fokus(fid: int, body: FocusIn, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    f = await db.get(DofbFocus, fid)
    if f is None:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    f.updated_at = datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "fokus", body.title)
    await db.commit()
    return _focus_out(f)


@router.delete("/fokus/{fid}")
async def slet_fokus(fid: int, ctx: svc.Ctx = Depends(need("dofbasen_news", "nyheder")), db: AsyncSession = Depends(get_session)):
    f = await db.get(DofbFocus, fid)
    if f:
        await svc.audit(db, ctx.user, "slet fokus", f.title)
        await db.delete(f)
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- admin-panel


@router.get("/admin/oversigt")
async def admin_oversigt(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    if not ctx.perms:
        raise HTTPException(403, "Kræver en DOFbasen-rolle")
    counts = await _row(db, """SELECT (SELECT count(*) FROM dofb_pages) AS sider, (SELECT count(*) FROM dofb_news) AS nyheder,
        (SELECT count(*) FROM dofb_lists) AS lister, (SELECT count(*) FROM dofb_areas) AS omraader,
        (SELECT count(*) FROM dofb_user_roles) AS roller, (SELECT count(*) FROM dofb_api_grants) AS api_brugere,
        (SELECT count(*) FROM dofb_api_tokens WHERE revoked_at IS NULL) AS tokens,
        (SELECT coalesce(sum(uses), 0) FROM dofb_api_tokens) AS api_kald, (SELECT count(*) FROM dofb_obs_status) AS dku""")
    log = await _rows(db, """SELECT a.at, a.action, a.target, u.navn, u.obserkode FROM dofb_audit a LEFT JOIN users u ON u.id = a.user_id
                             ORDER BY a.at DESC LIMIT 40""")
    flags = await features.load(db)
    return {"counts": counts, "log": log, "features": [{"key": k, "name": v[0], "description": v[1], "state": flags[k]}
                                                       for k, v in features.FEATURES.items()], "perms": svc.PERMS}


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    perms: list[str] = Field(default_factory=list)


@router.get("/admin/roller")
async def admin_roller(ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    roles = [{"key": r.key, "name": r.name, "perms": r.perms} for r in (await db.execute(select(DofbRole).order_by(DofbRole.sort, DofbRole.name))).scalars()]
    people = await _rows(db, """
        SELECT u.id, u.navn, u.obserkode, array_agg(ur.role_key ORDER BY ur.role_key) AS roles, max(ur.granted_at) AS granted_at
        FROM dofb_user_roles ur JOIN users u ON u.id = ur.user_id GROUP BY u.id ORDER BY u.navn""")
    return {"roles": roles, "people": people, "perms": svc.PERMS}


@router.post("/admin/roller")
async def ny_rolle(body: RoleIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    import re

    key = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_")[:32] or "rolle"
    base, i = key, 2
    while await db.get(DofbRole, key):
        key, i = f"{base[:28]}_{i}", i + 1
    db.add(DofbRole(key=key, name=body.name, perms=[p for p in body.perms if p in svc.PERMS]))
    await svc.audit(db, ctx.user, "ny rolle", body.name)
    await db.commit()
    return {"key": key}


@router.patch("/admin/roller/{key}")
async def ret_rolle(key: str, body: RoleIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    r = await db.get(DofbRole, key)
    if r is None:
        raise HTTPException(404)
    r.name, r.perms = body.name, [p for p in body.perms if p in svc.PERMS]
    await svc.audit(db, ctx.user, "rolle", f"{body.name}: {', '.join(r.perms) or 'ingen'}")
    await db.commit()
    return {"ok": True}


@router.delete("/admin/roller/{key}")
async def slet_rolle(key: str, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    if key == "admin":
        raise HTTPException(400, "Administratorrollen kan ikke slettes")
    await db.execute(delete(DofbRole).where(DofbRole.key == key))
    await svc.audit(db, ctx.user, "slet rolle", key)
    await db.commit()
    return {"ok": True}


@router.get("/admin/brugere")
async def admin_brugere(q: str = Query(min_length=2, max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Find portalbrugere (til roller og API-adgang)."""
    if not (ctx.can("roller") or ctx.can("api")):
        raise HTTPException(403)
    return await _rows(db, """
        SELECT u.id, u.navn, u.obserkode, coalesce((SELECT array_agg(role_key) FROM dofb_user_roles WHERE user_id = u.id), '{}') AS roles,
               EXISTS (SELECT 1 FROM dofb_api_grants g WHERE g.user_id = u.id) AS api
        FROM users u WHERE (u.navn ILIKE :q OR u.obserkode ILIKE :q) AND u.approved AND NOT u.blocked ORDER BY u.navn LIMIT 20
    """, q=f"%{q}%")


class UserRolesIn(BaseModel):
    roles: list[str] = Field(max_length=20)


@router.put("/admin/brugere/{user_id}/roller")
async def saet_roller(user_id: int, body: UserRolesIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    await svc.set_user_roles(db, user_id, body.roles, ctx.user)
    await svc.audit(db, ctx.user, "roller", f"{target.obserkode}: {', '.join(body.roles) or 'ingen'}")
    await db.commit()
    return {"ok": True}


class GrantIn(BaseModel):
    rate_per_min: int = Field(60, ge=1, le=6000)
    max_tokens: int = Field(5, ge=1, le=50)
    note: str = Field("", max_length=300)


@router.get("/admin/api")
async def admin_api(ctx: svc.Ctx = Depends(need("dofbasen_api", "api")), db: AsyncSession = Depends(get_session)):
    grants = await _rows(db, """
        SELECT g.user_id, u.navn, u.obserkode, g.rate_per_min, g.max_tokens, g.note, g.granted_at, gb.navn AS granted_by,
               (SELECT count(*) FROM dofb_api_tokens t WHERE t.user_id = g.user_id AND t.revoked_at IS NULL) AS tokens,
               (SELECT coalesce(sum(uses), 0) FROM dofb_api_tokens t WHERE t.user_id = g.user_id) AS uses,
               (SELECT max(last_used_at) FROM dofb_api_tokens t WHERE t.user_id = g.user_id) AS last_used
        FROM dofb_api_grants g JOIN users u ON u.id = g.user_id LEFT JOIN users gb ON gb.id = g.granted_by ORDER BY u.navn""")
    tokens = await _rows(db, """
        SELECT t.id, t.user_id, u.obserkode, t.name, t.prefix, t.created_at, t.last_used_at, t.uses, t.revoked_at
        FROM dofb_api_tokens t JOIN users u ON u.id = t.user_id ORDER BY t.created_at DESC LIMIT 300""")
    return {"grants": grants, "tokens": tokens}


@router.put("/admin/api/{user_id}")
async def giv_api(user_id: int, body: GrantIn, ctx: svc.Ctx = Depends(need("dofbasen_api", "api")), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    g = await db.get(DofbApiGrant, user_id)
    if g is None:
        g = DofbApiGrant(user_id=user_id, granted_by=ctx.user.id)
        db.add(g)
    g.rate_per_min, g.max_tokens, g.note = body.rate_per_min, body.max_tokens, body.note or None
    await svc.audit(db, ctx.user, "API-adgang", f"{target.obserkode} ({body.rate_per_min}/min)")
    await db.commit()
    return {"ok": True}


@router.delete("/admin/api/{user_id}")
async def fjern_api(user_id: int, ctx: svc.Ctx = Depends(need("dofbasen_api", "api")), db: AsyncSession = Depends(get_session)):
    """Fjern adgangen og spær alle brugerens tokens."""
    await db.execute(delete(DofbApiGrant).where(DofbApiGrant.user_id == user_id))
    await db.execute(update(DofbApiToken).where(DofbApiToken.user_id == user_id, DofbApiToken.revoked_at.is_(None))
                     .values(revoked_at=func.now()))
    await svc.audit(db, ctx.user, "fjern API-adgang", str(user_id))
    await db.commit()
    return {"ok": True}


@router.delete("/admin/api/tokens/{token_id}")
async def spaer_token(token_id: int, ctx: svc.Ctx = Depends(need("dofbasen_api", "api")), db: AsyncSession = Depends(get_session)):
    await db.execute(update(DofbApiToken).where(DofbApiToken.id == token_id, DofbApiToken.revoked_at.is_(None)).values(revoked_at=func.now()))
    await svc.audit(db, ctx.user, "spær token", str(token_id))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- mine API-tokens


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("/me/api")
async def mine_tokens(user: User = Depends(require_user), ctx: svc.Ctx = Depends(need("dofbasen_api")), db: AsyncSession = Depends(get_session)):
    g = await db.get(DofbApiGrant, user.id)
    tokens = [{"id": t.id, "name": t.name, "prefix": t.prefix, "created_at": t.created_at, "last_used_at": t.last_used_at,
               "uses": t.uses, "revoked_at": t.revoked_at}
              for t in (await db.execute(select(DofbApiToken).where(DofbApiToken.user_id == user.id).order_by(DofbApiToken.created_at.desc()))).scalars()]
    return {"access": g is not None, "rate_per_min": g.rate_per_min if g else None, "max_tokens": g.max_tokens if g else None,
            "tokens": tokens}


@router.post("/me/api/tokens")
async def nyt_token(body: TokenIn, user: User = Depends(require_user), ctx: svc.Ctx = Depends(need("dofbasen_api")),
                    db: AsyncSession = Depends(get_session)):
    g = await db.get(DofbApiGrant, user.id)
    if g is None:
        raise HTTPException(403, "Du har ikke adgang til API'et. En administrator skal give dig adgang.")
    active = (await db.execute(select(func.count()).select_from(DofbApiToken)
                               .where(DofbApiToken.user_id == user.id, DofbApiToken.revoked_at.is_(None)))).scalar()
    if active >= g.max_tokens:
        raise HTTPException(400, f"Du kan højst have {g.max_tokens} aktive tokens. Slet et gammelt først.")
    token, prefix, digest = svc.new_token()
    t = DofbApiToken(user_id=user.id, name=body.name, prefix=prefix, token_hash=digest)
    db.add(t)
    await svc.audit(db, user, "nyt token", body.name)
    await db.commit()
    return {"id": t.id, "token": token, "prefix": prefix, "name": t.name}


@router.delete("/me/api/tokens/{token_id}")
async def slet_mit_token(token_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(update(DofbApiToken).where(DofbApiToken.id == token_id, DofbApiToken.user_id == user.id,
                                                DofbApiToken.revoked_at.is_(None)).values(revoked_at=func.now()))
    await db.commit()
    return {"ok": True}
