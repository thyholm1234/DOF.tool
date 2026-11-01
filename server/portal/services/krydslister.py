"""Krydslister: listetyper (kl_list_defs), automatiske kryds fra DOFbasen, manuelle kryds og visninger (docs/netfugl-afloesning-spec.md §11).

Logikken er kopieret fra showcasen (services/netfugl.py), men drevet af de redigerbare listetyper i stedet for en hårdkodet tabel. Showcasen beholder
sine nf_*-tabeller og røres ikke. De automatiske kryds beregnes af workeren (kryds_auto) og læses herfra; observationerne ligger i observationsgruppen
og læses i separate forespørgsler, aldrig i samme SQL som kl_*."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, cached, kv, version
from portal.config import SEED_DIR
from portal.db import ObsSession
from portal.models import KlKryds, KlListDef, KlSpeciesList, KlSpeciesListEntry, KlTaxon, User
from portal.services import lists
from portal.services.setting_store import get_setting_dict, put_setting
from portal.timeutil import dk_today

SETTINGS_KEY = "krydslister"
DEFAULT_SETTINGS = {"participants": "brugere", "aktive_max": 300, "year_lists": 5}
TEXTS_KEY = "krydslister_tekster"
VERSION = "klkryds"
MONTHS = ["Januar", "Februar", "Marts", "April", "Maj", "Juni", "Juli", "August", "September", "Oktober", "November", "December"]
SJAELDENHED = ("almindelig", "relativ_almindelig", "ualmindelig", "sjaelden", "su")
CONTENT = SEED_DIR / "netfugl" / "content.json"
# (key, navn, referenceliste, auto, manuel, param, regler)
SEED_DEFS = [
    ("dk", "Danske Arter", "dk", True, True, None, {"kategorier": ["A", "B", "C"]}),
    ("aar", "Danske Årsarter {p}", "dk", True, False, "year", {"kategorier": ["A", "B", "C"]}),
    ("md", "Danske Månedsarter {p}", "dk", True, False, "month", {"kategorier": ["A", "B", "C"]}),
    ("afd", "Lokalarter: {p}", "dk", True, False, "afdeling", {"kategorier": ["A", "B", "C"]}),
    ("kom", "Lokalarter: {p}", "dk", True, False, "kommune", {"kategorier": ["A", "B", "C"]}),
    ("ring", "Danske Ringmærkningsarter", "dk", False, True, None, {"kun_ringmaerkning": True}),
    ("aedel", "Danske Ædelarter", "dk", False, True, None, {}),
    ("aedelaar", "Danske Ædelårsarter {p}", "dk", False, False, "year", {"kilde_liste": "aedel"}),
    ("vp", "VP Artsliste", "vp", False, True, None, {"auto_fra": "dk"}),
    ("falskvp", "Falsk VP Artsliste", "vp", False, True, None, {"godkendelse": True}),
]


class KlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def settings(session: AsyncSession) -> dict:
    return await get_setting_dict(session, SETTINGS_KEY, DEFAULT_SETTINGS)


# ---------------------------------------------------------------- seed

async def seed(session: AsyncSession) -> bool:
    """Læg World Bird List, DK/VP-listen og de ti listetyper ind fra showcasens data, hvis modulet er tomt. Returnerer True, hvis noget blev lagt ind."""
    if (await session.execute(select(KlSpeciesList.id).limit(1))).first() is not None:
        return False
    dk, vp = [], []  # (sort, navn, latin, engelsk, kategori, su, artnr)
    if (await session.execute(text("SELECT to_regclass('nf_species')"))).scalar() is not None and (await session.execute(text("SELECT count(*) FROM nf_species"))).scalar():
        for r in (await session.execute(text("SELECT liste, sort, navn, latin, engelsk, kategori, su, artnr FROM nf_species ORDER BY liste, sort"))).all():
            (dk if r[0] == "dk" else vp).append((r[1], r[2], r[3], r[4], r[5], bool(r[6]), r[7]))
    elif Path(CONTENT).is_file():
        data = json.loads(Path(CONTENT).read_text(encoding="utf-8"))
        for i, (su, navn, lat, kat) in enumerate(data["dk_list"]):
            dk.append((i, navn, lat, None, kat, su == "*", None))
        for i, (_, star, navn, lat, eng) in enumerate(data["vp_list"]):
            vp.append((i, navn, lat, eng, None, star == "*", None))
    latin = {r[1].lower(): r[0] for r in (await session.execute(text(
        "SELECT artnr, latin FROM species WHERE latin IS NOT NULL AND art_type IN ('art', 'underart') ORDER BY art_type = 'art' DESC"))).all() if r[1]}
    names = {r[1].lower(): r[0] for r in (await session.execute(text("SELECT artnr, navn FROM species WHERE art_type = 'art'"))).all()}
    taxa: dict[str, KlTaxon] = {}

    async def taxon(navn: str, lat: str, eng: str | None, artnr: int | None, sort: int) -> KlTaxon:
        key = lat.strip().lower()
        t = taxa.get(key)
        if t is None:
            t = KlTaxon(latin=lat.strip(), dansk=navn, engelsk=eng, artnr=artnr or latin.get(key) or names.get(navn.lower()), sort=sort, version="netfugl")
            session.add(t)
            await session.flush()
            taxa[key] = t
        else:
            t.engelsk = t.engelsk or eng
            t.dansk = t.dansk or navn
        return t
    lists: dict[str, KlSpeciesList] = {}
    for i, (key, navn, desc) in enumerate([("dk", "DK listen", "Arter truffet i Danmark med kategori (A–F) og sjældenhed."), ("vp", "VP listen", "Arter truffet i Vestpalæarktis. * = generelt sjælden.")]):
        lists[key] = KlSpeciesList(key=key, navn=navn, beskrivelse=desc, sort=i)
        session.add(lists[key])
    await session.flush()
    seen: set[tuple[str, int]] = set()  # kilden har enkelte dubletter (samme latinske navn to gange): første række gælder
    for i, (sort, navn, lat, eng, kat, su, artnr) in enumerate(vp):
        t = await taxon(navn, lat, eng, artnr, sort)
        if ("vp", t.id) not in seen:
            seen.add(("vp", t.id))
            session.add(KlSpeciesListEntry(list_id=lists["vp"].id, taxon_id=t.id, kategorier=[], sjaeldenhed="su" if su else "almindelig", sort=sort))
    for sort, navn, lat, eng, kat, su, artnr in dk:
        t = await taxon(navn, lat, eng, artnr, sort + 100000)
        if ("dk", t.id) not in seen:
            seen.add(("dk", t.id))
            session.add(KlSpeciesListEntry(list_id=lists["dk"].id, taxon_id=t.id, kategorier=[kat] if kat else [], sjaeldenhed="su" if su else "almindelig", sort=sort))
    for i, (key, navn, ref, auto, manuel, param, regler) in enumerate(SEED_DEFS):
        session.add(KlListDef(key=key, navn=navn, ref_list_id=lists[ref].id, auto=auto, manuel=manuel, param=param, regler=regler, sort=i * 10))
    await session.commit()
    await bump(VERSION)
    return True


# ---------------------------------------------------------------- listetyper

async def defs(session: AsyncSession, *, only_active: bool = True) -> dict[str, dict]:
    """key → listetype (med referencelistens nøgle). Cachet i Valkey, indtil listetyperne ændres."""
    async def produce():
        rows = (await session.execute(text("""
            SELECT d.id, d.key, d.navn, d.auto, d.manuel, d.param, d.regler, d.sort, d.aktiv, d.beskrivelse_md, d.ref_list_id, l.key AS ref
            FROM kl_list_defs d LEFT JOIN kl_species_lists l ON l.id = d.ref_list_id ORDER BY d.sort, d.id"""))).mappings().all()
        return {r["key"]: dict(r) for r in rows}
    out = await cached(f"kl:defs:{await version(VERSION)}", 3600, produce)
    return {k: v for k, v in out.items() if v["aktiv"]} if only_active else out


async def parse_list(session: AsyncSession, key: str) -> tuple[dict, str | None]:
    """'aar:2026' → (listetype, '2026'). KlError ved ukendt liste eller ugyldig parameter."""
    base, _, param = key.partition(":")
    d = (await defs(session)).get(base)
    if d is None:
        raise KlError(404, f"Ukendt krydsliste {key}")
    kind = d["param"]
    if kind and not param:
        raise KlError(400, f"Krydslisten {base} kræver {kind}")
    if kind == "year" and not (param.isdigit() and 1900 < int(param) < 2100):
        raise KlError(400, "Ugyldigt år")
    if kind == "month" and not (param.isdigit() and 1 <= int(param) <= 12):
        raise KlError(400, "Ugyldig måned")
    if kind == "afdeling" and not param.isdigit():
        raise KlError(400, "Ugyldig afdeling")
    if kind == "kommune" and not re.fullmatch(r"\d{3,4}", param):
        raise KlError(400, "Ugyldig kommune")
    return d, param or None


async def list_name(session: AsyncSession, d: dict, p: str | None) -> str:
    if d["param"] == "month" and p:
        p = MONTHS[int(p) - 1]
    elif d["param"] == "afdeling" and p:
        p = (await session.execute(text("SELECT navn FROM afdelinger WHERE id = :i"), {"i": int(p)})).scalar() or p
    elif d["param"] == "kommune" and p:
        p = (await session.execute(text("SELECT navn FROM kommuner WHERE kode = :k"), {"k": p})).scalar() or p
    return d["navn"].format(p=p or "")


# ---------------------------------------------------------------- deltagere og automatiske kryds

async def participants(session: AsyncSession) -> dict[str, str]:
    """obserkode → navn. «brugere»: portalens brugere, der viser deres lister. «aktive»: de mest aktive DOFbasen-observatører (demo)."""
    st = await settings(session)
    out = {p["obserkode"]: p["navn"] for p in await lists.participants(session)}  # samme deltagere som Lister
    if st["participants"] == "aktive":
        for kode, navn in json.loads(await kv().get("kl:aktive") or "[]"):
            out.setdefault(kode, navn)
    return out


async def refresh_active(session: AsyncSession) -> None:
    st = await settings(session)
    async with ObsSession() as obs:
        await obs.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
        rows = (await obs.execute(text(f"""
            SELECT o.obserkode, trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS navn
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN observers ob ON ob.obserkode = o.obserkode
            WHERE o.dato >= :d AND s.list_artnr IS NOT NULL AND o.antal > 0 AND o.hemmelig = 0
            GROUP BY o.obserkode, ob.fornavn, ob.efternavn ORDER BY count(DISTINCT s.list_artnr) DESC LIMIT {int(st['aktive_max'])}"""),
                                 {"d": dk_today() - timedelta(days=3 * 365)})).all()
    await kv().set("kl:aktive", json.dumps([[r[0], r[1] or r[0]] for r in rows]))


def _countable(regler: dict) -> str | None:
    cats = [c for c in (regler.get("kategorier") or ["A", "B", "C"]) if c in "ABCDEF"]
    if cats == ["A", "B", "C"]:
        return None  # standard (lists.counts_sql): kun arter med kategori, overstyring eller [klammer] slås op (hurtigt)
    return "bird_category(s.artnr, s.list_artnr, o.loknr, o.dato) IN (" + ", ".join(f"'{c}'" for c in cats) + ")"


def auto_queries(defs_: dict[str, dict]) -> list[tuple[str, dict]]:
    """(SQL, regler) for hver automatisk listetype. Kolonner: obserkode, liste, artnr, dato, obsid, loknr."""
    out = []
    for d in defs_.values():
        if not d["auto"]:
            continue
        key, countable = d["key"], _countable(d["regler"])
        base = f"""FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
            WHERE o.obserkode = ANY(:codes) AND {lists.counts_sql(countable)}"""
        if d["param"] is None:
            out.append((f"""SELECT DISTINCT ON (o.obserkode, s.list_artnr) o.obserkode, '{key}', s.list_artnr, o.dato, o.obsid, o.loknr {base}
                ORDER BY o.obserkode, s.list_artnr, o.dato, o.obsid""", d))
        elif d["param"] == "year":
            out.append((f"""SELECT DISTINCT ON (o.obserkode, extract(year FROM o.dato), s.list_artnr) o.obserkode, '{key}:' || extract(year FROM o.dato)::int,
                s.list_artnr, o.dato, o.obsid, o.loknr {base} AND o.dato >= :y0 ORDER BY o.obserkode, extract(year FROM o.dato), s.list_artnr, o.dato, o.obsid""", d))
        elif d["param"] == "month":
            out.append((f"""SELECT DISTINCT ON (o.obserkode, extract(month FROM o.dato), s.list_artnr) o.obserkode, '{key}:' || extract(month FROM o.dato)::int,
                s.list_artnr, o.dato, o.obsid, o.loknr {base} ORDER BY o.obserkode, extract(month FROM o.dato), s.list_artnr, o.dato, o.obsid""", d))
        elif d["param"] == "afdeling":
            out.append((f"""SELECT DISTINCT ON (o.obserkode, a.afd, s.list_artnr) o.obserkode, '{key}:' || a.afd, s.list_artnr, o.dato, o.obsid, o.loknr
                {base.replace('WHERE', 'CROSS JOIN LATERAL unnest(o.afdelinger) AS a(afd) WHERE', 1)} ORDER BY o.obserkode, a.afd, s.list_artnr, o.dato, o.obsid""", d))
        elif d["param"] == "kommune":
            out.append((f"""SELECT DISTINCT ON (o.obserkode, l.kommune_kode, s.list_artnr) o.obserkode, '{key}:' || l.kommune_kode, s.list_artnr, o.dato, o.obsid, o.loknr
                {base.replace('WHERE', 'JOIN locations l ON l.loknr = o.loknr WHERE l.kommune_kode IS NOT NULL AND', 1)}
                ORDER BY o.obserkode, l.kommune_kode, s.list_artnr, o.dato, o.obsid""", d))
    return out


async def refresh_auto(session: AsyncSession) -> int:
    """Genberegn de automatiske kryds for alle deltagere (første fund pr. art pr. liste). Returnerer antal kryds."""
    st = await settings(session)
    if st["participants"] == "aktive":
        await refresh_active(session)
    codes = sorted(await participants(session))
    await session.execute(text("DELETE FROM kl_auto_lists WHERE NOT (obserkode = ANY(:c))"), {"c": codes})
    await session.commit()
    y0 = date(dk_today().year - int(st["year_lists"]) + 1, 1, 1)
    queries = auto_queries(await defs(session))
    total = 0
    for i in range(0, len(codes), 25):
        batch = codes[i:i + 25]
        lists: dict[tuple[str, str], list[list]] = {}
        async with ObsSession() as obs:
            await obs.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
            for q, _ in queries:
                for kode, liste, artnr, dato, obsid, loknr in (await obs.execute(text(q), {"codes": batch, "y0": y0})).all():
                    cols = lists.setdefault((kode, liste), [[], [], [], []])
                    for col, v in zip(cols, (artnr, dato, obsid, loknr)):
                        col.append(v)
                    total += 1
        await session.execute(text("DELETE FROM kl_auto_lists WHERE obserkode = ANY(:c)"), {"c": batch})
        if lists:
            await session.execute(text("INSERT INTO kl_auto_lists (obserkode, liste, artnr, dato, obsid, loknr) VALUES (:k, :l, :a, :d, :o, :lok)"),
                                  [{"k": k, "l": li, "a": a, "d": d, "o": o, "lok": lok} for (k, li), (a, d, o, lok) in lists.items()])
        await session.commit()
    await bump(VERSION)
    await kv().set("kl:auto:at", datetime.now(timezone.utc).isoformat())
    return total


async def auto_at() -> str | None:
    return await kv().get("kl:auto:at")


# ---------------------------------------------------------------- læsning

def _skey(by_artnr: bool, artnr: int | None, latin: str | None) -> str:
    return f"a{artnr}" if by_artnr and artnr else f"l{(latin or '').lower()}"


async def load_kryds(session: AsyncSession, key: str) -> dict:
    """Alle deltageres kryds på en liste: {people, rows: [{kode, key, navn, latin, dato, lok, land, obsid, kilde}], name}.
    key i rækkerne er artens nøgle: «a<artnr>» når referencelisten er DK, ellers «l<latin>»."""
    d, p = await parse_list(session, key)

    async def produce() -> dict:
        people = await participants(session)
        codes = list(people)
        by_artnr = d["ref"] == "dk"
        regler = d["regler"] or {}
        rows: list[dict] = []
        auto_key = key if d["auto"] else (regler.get("auto_fra") or None)
        if auto_key:
            auto = (await session.execute(text("""
                SELECT a.obserkode, u.artnr, s.navn, s.latin, u.dato, u.obsid, u.loknr, l.navn AS lok
                FROM kl_auto_lists a CROSS JOIN LATERAL unnest(a.artnr, a.dato, a.obsid, a.loknr) AS u(artnr, dato, obsid, loknr)
                JOIN species s ON s.artnr = u.artnr LEFT JOIN locations l ON l.loknr = u.loknr
                WHERE a.liste = :l AND a.obserkode = ANY(:c)"""), {"l": auto_key, "c": codes})).mappings().all()
            for r in auto:
                rows.append({"kode": r["obserkode"], "key": _skey(by_artnr, r["artnr"], r["latin"]), "artnr": r["artnr"], "navn": r["navn"], "latin": r["latin"],
                             "dato": r["dato"].isoformat(), "lok": r["lok"], "land": "Danmark", "obsid": r["obsid"], "kilde": "dofbasen"})
        manual_list = regler.get("kilde_liste") or d["key"]
        if d["manuel"] or regler.get("kilde_liste"):
            q = "SELECT * FROM kl_kryds WHERE liste = :l AND obserkode = ANY(:c) AND status = 'ok'"
            args = {"l": manual_list, "c": codes}
            if d["param"] == "year" and p:
                q += " AND dato BETWEEN :a AND :b"
                args.update(a=date(int(p), 1, 1), b=date(int(p), 12, 31))
            man = (await session.execute(text(q), args)).mappings().all()
            hidden = {(m["obserkode"], _skey(by_artnr, m["artnr"], m["latin"])) for m in man if m["hidden"]}
            rows = [r for r in rows if (r["kode"], r["key"]) not in hidden]
            for m in man:
                if m["hidden"]:
                    continue
                rows.append({"kode": m["obserkode"], "key": _skey(by_artnr, m["artnr"], m["latin"]), "artnr": m["artnr"], "navn": m["navn"], "latin": m["latin"],
                             "dato": m["dato"].isoformat() if m["dato"] else None, "lok": m["lokalitet"], "land": m["land"], "obsid": None, "kilde": "manuel",
                             "id": m["id"], "note": m["note"]})
        best: dict[tuple[str, str], dict] = {}
        for r in rows:
            k = (r["kode"], r["key"])
            if k not in best or (r["dato"] or "9999") < (best[k]["dato"] or "9999"):
                best[k] = r
        return {"people": people, "rows": list(best.values()), "name": await list_name(session, d, p)}

    return await cached(f"kl:kryds:{key}:{await version(VERSION)}", 6 * 3600, produce)


async def merge_life_list(session: AsyncSession, obserkode: str, items: list[dict]) -> list[dict]:
    """Livslisten for Danmark (Lister → Mine lister) og krydslisten «dk» er samme liste: deltagerens manuelle DK-kryds lægges til,
    og automatiske kryds, deltageren har skjult (fx fejlbestemmelser), trækkes fra. items er lists.year_list-rækker (artnr = listeart)."""
    man = (await session.execute(text("""
        SELECT artnr, dato, lokalitet, hidden FROM kl_kryds WHERE liste = 'dk' AND obserkode = :k AND status = 'ok' AND artnr IS NOT NULL"""),
        {"k": obserkode})).mappings().all()
    if not man:
        return items
    sp = {r[0]: r for r in (await session.execute(text("""
        SELECT s.artnr, s.list_artnr, ls.navn, ls.klass, ls.sortering FROM species s JOIN species ls ON ls.artnr = s.list_artnr WHERE s.artnr = ANY(:a)"""),
        {"a": list({m["artnr"] for m in man})})).all()}
    hidden = {sp[m["artnr"]][1] for m in man if m["hidden"] and m["artnr"] in sp}
    out = [i for i in items if i["artnr"] not in hidden]
    have = {i["artnr"] for i in out}
    for m in man:
        if m["hidden"] or m["artnr"] not in sp or sp[m["artnr"]][1] in have:
            continue
        _, la, navn, klass, sortering = sp[m["artnr"]]
        have.add(la)
        out.append({"artnr": la, "art": navn, "klass": klass, "sortering": sortering, "dato": m["dato"].isoformat() if m["dato"] else None,
                    "loknr": None, "lok": m["lokalitet"], "obsid": None, "kilde": "manuel"})
    out.sort(key=lambda r: (r["dato"] or "9999", r["obsid"] or 0))
    return out


def ranking(data: dict) -> list[dict]:
    per: dict[str, list[dict]] = {}
    for r in data["rows"]:
        per.setdefault(r["kode"], []).append(r)
    out = []
    for kode, rows in per.items():
        last = max(rows, key=lambda r: r["dato"] or "")
        out.append({"kode": kode, "navn": data["people"].get(kode, kode), "antal": len(rows), "opdateret": last["dato"], "seneste": last["navn"], "seneste_land": last.get("land")})
    out.sort(key=lambda r: (-r["antal"], r["navn"].lower()))
    place, prev = 0, None
    for i, r in enumerate(out, start=1):
        if r["antal"] != prev:
            place, prev = i, r["antal"]
        r["placering"] = place
    return out


def latest(data: dict, n: int = 100, min_count: int = 0) -> list[dict]:
    """Seneste n kryds med personens antal (#X) efter krydset."""
    per: dict[str, list[dict]] = {}
    undated_n: dict[str, int] = {}
    for r in data["rows"]:
        if r["dato"]:
            per.setdefault(r["kode"], []).append(r)
        else:
            undated_n[r["kode"]] = undated_n.get(r["kode"], 0) + 1
    out = []
    for kode, rows in per.items():
        rows.sort(key=lambda r: r["dato"])
        for i, r in enumerate(rows, start=1):
            out.append({**r, "x": i + undated_n.get(kode, 0), "person": data["people"].get(kode, kode)})
    out = [r for r in out if r["x"] >= min_count]
    out.sort(key=lambda r: (r["dato"], r["x"]), reverse=True)
    return out[:n]


def who_has_what(data: dict, reference: list[dict]) -> dict:
    count: dict[str, int] = {}
    names: dict[str, dict] = {}
    for r in data["rows"]:
        count[r["key"]] = count.get(r["key"], 0) + 1
        names.setdefault(r["key"], r)
    total = len({r["kode"] for r in data["rows"]})
    rows, seen = [], set()
    for s in reference:
        seen.add(s["key"])
        rows.append({"key": s["key"], "navn": s["navn"], "latin": s["latin"], "su": s.get("su"), "antal": count.get(s["key"], 0)})
    for k, r in names.items():
        if k not in seen:
            rows.append({"key": k, "navn": r["navn"], "latin": r["latin"], "su": False, "antal": count[k]})
    return {"total": total, "rows": rows}


def blockers(data: dict) -> dict:
    total = len({r["kode"] for r in data["rows"]})
    limit = max(1, int(total * 0.10))
    count: dict[str, int] = {}
    names: dict[str, dict] = {}
    for r in data["rows"]:
        count[r["key"]] = count.get(r["key"], 0) + 1
        names.setdefault(r["key"], r)
    rows = [{"key": k, "navn": names[k]["navn"], "latin": names[k]["latin"], "antal": n} for k, n in count.items() if n <= limit]
    rows.sort(key=lambda r: (r["antal"], r["navn"]))
    keys = {r["key"] for r in rows}
    per: dict[str, list[str]] = {}
    for r in data["rows"]:
        if r["key"] in keys:
            per.setdefault(r["kode"], []).append(r["key"])
    stat = [{"kode": kode, "navn": data["people"].get(kode, kode), "antal": sum(1 for r in data["rows"] if r["kode"] == kode), "blockers": len(per.get(kode, []))}
            for kode in {r["kode"] for r in data["rows"]}]
    stat.sort(key=lambda r: (-r["blockers"], -r["antal"], r["navn"].lower()))
    return {"total": total, "limit": limit, "rows": rows, "stat": [s for s in stat if s["blockers"]]}


def tophits(data: dict, year: int) -> list[dict]:
    y = str(year)
    in_year: dict[str, int] = {}
    total: dict[str, int] = {}
    names: dict[str, dict] = {}
    for r in data["rows"]:
        total[r["key"]] = total.get(r["key"], 0) + 1
        names.setdefault(r["key"], r)
        if (r["dato"] or "").startswith(y):
            in_year[r["key"]] = in_year.get(r["key"], 0) + 1
    rows = [{"key": k, "navn": names[k]["navn"], "latin": names[k]["latin"], "aar": n, "total": total[k]} for k, n in in_year.items()]
    rows.sort(key=lambda r: (-r["aar"], r["navn"]))
    return rows[:50]


async def reference(session: AsyncSession, list_id: int | None, by_artnr: bool) -> list[dict]:
    """Referencelisten (rækkefølge og SU-markering) til «Hvem har set hvad?»."""
    rows = (await session.execute(text("""
        SELECT t.id, t.dansk, t.engelsk, t.latin, t.artnr, e.kategorier, e.sjaeldenhed FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id
        WHERE e.list_id = :l ORDER BY e.sort, t.id"""), {"l": list_id})).mappings().all()
    return [{"key": _skey(by_artnr, r["artnr"], r["latin"]), "navn": r["dansk"] or r["engelsk"] or r["latin"], "latin": r["latin"], "su": r["sjaeldenhed"] == "su",
             "kategori": ",".join(r["kategorier"] or []), "artnr": r["artnr"], "id": r["id"]} for r in rows]


async def overview_lists(session: AsyncSession) -> dict:
    """Til «Alle krydslister»: listetyperne og de parametre, der kan vælges."""
    y = dk_today().year
    st = await settings(session)
    ds = await defs(session)
    aedel_years = [r[0] for r in (await session.execute(text(
        "SELECT DISTINCT extract(year FROM dato)::int FROM kl_kryds WHERE liste = 'aedel' AND dato IS NOT NULL AND status = 'ok' ORDER BY 1 DESC"))).all()]
    return {"lister": [{"key": k, "name": v["navn"], "auto": v["auto"], "manual": v["manuel"] or bool(v["regler"].get("kilde_liste")), "param": v["param"], "ref": v["ref"],
                        "beskrivelse": v["beskrivelse_md"]} for k, v in ds.items()],
            "years": list(range(y, y - int(st["year_lists"]), -1)), "aedel_years": aedel_years or [y], "months": MONTHS,
            "afdelinger": [dict(r) for r in (await session.execute(text("SELECT id, navn FROM afdelinger ORDER BY navn"))).mappings()],
            "kommuner": [dict(r) for r in (await session.execute(text("SELECT kode, navn FROM kommuner ORDER BY navn"))).mappings()],
            "auto_at": await auto_at(), "participants": st["participants"]}


# ---------------------------------------------------------------- mine kryds

async def my_kryds(session: AsyncSession, user: User, key: str) -> dict:
    d, _ = await parse_list(session, key)
    if not (d["manuel"] or d["auto"]):
        raise KlError(400, "Listen har ingen egne kryds")
    data = await load_kryds(session, key)
    rows = sorted((r for r in data["rows"] if r["kode"] == user.obserkode), key=lambda r: r["dato"] or "")
    hidden = [dict(r) for r in (await session.execute(text("SELECT id, artnr, navn, latin FROM kl_kryds WHERE obserkode = :k AND liste = :l AND hidden"),
                                                      {"k": user.obserkode, "l": d["key"]})).mappings()]
    pending = [dict(r) for r in (await session.execute(text("SELECT id, navn, latin, dato, lokalitet, land, status FROM kl_kryds WHERE obserkode = :k AND liste = :l AND status <> 'ok' AND NOT hidden"),
                                                       {"k": user.obserkode, "l": d["key"]})).mappings()]
    for r in pending:
        r["dato"] = r["dato"].isoformat() if r["dato"] else None
    return {"rows": rows, "hidden": hidden, "ventende": pending, "participant": user.obserkode in data["people"], "name": data["name"], "kan_krydse": d["manuel"],
            "godkendelse": bool((d["regler"] or {}).get("godkendelse")), "ref": d["ref"], "kan_skjule": d["auto"] or bool((d["regler"] or {}).get("auto_fra"))}


async def add_kryds(session: AsyncSession, user: User, *, liste: str, taxon_id: int, dato: date | None, lokalitet: str, land: str, note: str, privileged: bool) -> KlKryds:
    d, _ = await parse_list(session, liste)
    if not d["manuel"]:
        raise KlError(400, "Listen tillader ikke manuelle kryds")
    t = (await session.execute(text("""
        SELECT t.id, t.latin, t.dansk, t.engelsk, t.artnr, e.sjaeldenhed FROM kl_taxa t JOIN kl_species_list_entries e ON e.taxon_id = t.id AND e.list_id = :l WHERE t.id = :t"""),
                              {"l": d["ref_list_id"], "t": taxon_id})).mappings().first()
    if t is None:
        raise KlError(400, "Arten findes ikke på listen")
    if dato and dato > dk_today():
        raise KlError(400, "Datoen ligger i fremtiden")
    su = t["sjaeldenhed"] == "su"
    if d["ref"] == "vp" and su and not (dato and lokalitet and land):
        raise KlError(400, "Sjældne arter (*) kræver land, lokalitet og dato")
    if d["ref"] == "dk" and su and not (dato and lokalitet):
        raise KlError(400, "SU-arter kræver dato og lokalitet")
    await session.execute(text("DELETE FROM kl_kryds WHERE obserkode = :k AND liste = :l AND latin = :la"), {"k": user.obserkode, "l": d["key"], "la": t["latin"]})
    status = "afventer" if (d["regler"] or {}).get("godkendelse") and not privileged else "ok"
    row = KlKryds(obserkode=user.obserkode, liste=d["key"], taxon_id=t["id"], artnr=t["artnr"], latin=t["latin"], navn=t["dansk"] or t["engelsk"] or t["latin"], dato=dato,
                  lokalitet=lokalitet or None, land=land or ("Danmark" if d["ref"] == "dk" else None), note=note or None, status=status)
    session.add(row)
    await session.commit()
    await bump(VERSION)
    return row


async def hide_kryds(session: AsyncSession, user: User, liste: str, artnr: int, hidden: bool) -> None:
    d, _ = await parse_list(session, liste)
    sp = (await session.execute(text("SELECT navn, latin FROM species WHERE artnr = :a"), {"a": artnr})).first()
    if sp is None:
        raise KlError(404, "Arten findes ikke")
    await session.execute(text("DELETE FROM kl_kryds WHERE obserkode = :k AND liste = :l AND artnr = :a AND hidden"), {"k": user.obserkode, "l": d["key"], "a": artnr})
    if hidden:
        session.add(KlKryds(obserkode=user.obserkode, liste=d["key"], artnr=artnr, latin=sp[1] or sp[0], navn=sp[0], hidden=True))
    await session.commit()
    await bump(VERSION)


async def delete_kryds(session: AsyncSession, user: User, kid: int, privileged: bool) -> KlKryds:
    k = await session.get(KlKryds, kid)
    if k is None or (k.obserkode != user.obserkode and not privileged):
        raise KlError(404, "Krydset findes ikke")
    await session.delete(k)
    await session.commit()
    await bump(VERSION)
    return k
