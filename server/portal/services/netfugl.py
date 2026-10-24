"""Netfugl-modulet: en showcase af en ny netfugl.dk, integreret med DOFbasen-data i portalen.

* Indhold (tekstsider, ordforklaringer, DK-/VP-listen, fuglefamilier, links, RSS-kilder, artikel-uddrag) ligger i
  nf_*-tabellerne og lægges ind fra seed/netfugl/content.json første gang. Kopieret fra netfugl.dk som eksempel.
* Krydslister beregnes automatisk: workeren finder hver deltagers første fund pr. art i DOFbasen (DK, årsarter,
  månedsarter, lokalafdelinger og kommuner) og gemmer dem i nf_auto. Manuelle kryds (nf_kryds) supplerer: arter fra før
  DOFbasen, VP-arter, ringmærkningsarter og ædelarter – og kan skjule et automatisk kryds.
* RSS (Netfugl live): workeren henter kilderne hver time; mangler feed-adressen, findes den via <link rel="alternate">.
* Modulet har egne roller (nf_roles) med rettigheder (PERMS). Superadmin og portal-modulet "netfugl" har alle.
"""

from __future__ import annotations

import email.utils
import html
import html.entities
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv, version, bump
from portal.config import SEED_DIR, get_settings
from portal.db import ObsSession
from portal.models import (NfArticle, NfAudit, NfFamily, NfFeed, NfLink, NfPage, NfRole, NfSpecies,
                           NfUserRole, Setting, User)
from portal.services import features
from portal.services.auth import SUPERADMIN, can
from portal.services.categories import countable_sql
from portal.timeutil import dk_today

log = logging.getLogger("portal.netfugl")
CONTENT = SEED_DIR / "netfugl" / "content.json"

PERMS = {
    "artikler": "Nyheder og artikler (også kort nyt)",
    "sider": "Tekstsider, ordforklaringer og links",
    "fotos": "Fotomoderation: godkend og afvis fotos",
    "kommentarer": "Kommentarmoderation: skjul kommentarer",
    "kryds": "Krydslister og artslister: ret kryds og DK-/VP-listen",
    "rss": "RSS-kilder",
    "api": "API: tildel og fjern brugeres adgang",
    "roller": "Netfugl-roller: giv og fjern roller",
}
DEFAULT_ROLES = [
    ("admin", "Netfugl-administrator", list(PERMS)),
    ("redaktoer", "Redaktør", ["artikler", "sider", "rss"]),
    ("fotomoderator", "Fotomoderator", ["fotos", "kommentarer"]),
    ("listeansvarlig", "Listeansvarlig", ["kryds"]),
    ("api", "API-ansvarlig", ["api"]),
]
SETTINGS_KEY = "netfugl"
DEFAULT_SETTINGS = {"participants": "brugere", "aktive_max": 300, "photo_moderation": True, "year_lists": 5}


async def settings(session: AsyncSession) -> dict:
    row = await session.get(Setting, SETTINGS_KEY)
    return {**DEFAULT_SETTINGS, **(row.value if row is not None and isinstance(row.value, dict) else {})}


# ---------------------------------------------------------------- seed


async def seed(session: AsyncSession) -> None:
    """Læg standardindhold og -roller ind, hvis de mangler (eksisterende rækker røres ikke)."""
    if (await session.execute(select(NfRole.key).limit(1))).first() is None:
        for i, (key, name, perms) in enumerate(DEFAULT_ROLES):
            session.add(NfRole(key=key, name=name, perms=perms, sort=i * 10))
    if not CONTENT.is_file():
        await session.commit()
        return
    data = json.loads(CONTENT.read_text(encoding="utf-8"))
    have = set((await session.execute(select(NfPage.slug))).scalars())
    for slug, p in data["pages"].items():
        if slug not in have:
            session.add(NfPage(slug=slug, title=p["title"], body=p["body"]))
    if "ordforklaringer" not in have:
        body = "\n\n".join(f"**{t}** {e}" for t, e in data["ordforklaringer"])
        session.add(NfPage(slug="ordforklaringer", title="Ordforklaringer", body=body))
    if "kategorier" not in have:
        body = "\n\n".join(f"**Kategori {k}** {t}" for k, t in data["kategorier"])
        session.add(NfPage(slug="kategorier", title="Kategorier", body=body))
    if (await session.execute(select(NfSpecies.id).limit(1))).first() is None:
        latin = {r[1].lower(): r[0] for r in (await session.execute(text(
            "SELECT artnr, latin FROM species WHERE latin IS NOT NULL AND art_type IN ('art', 'underart') ORDER BY art_type = 'art' DESC"))).all()
            if r[1]}
        # Navnematch som reserve, når taksonomien har skiftet slægtsnavn (fx Curruca/Sylvia)
        names = {r[1].lower(): r[0] for r in (await session.execute(text("SELECT artnr, navn FROM species WHERE art_type = 'art'"))).all()}
        for i, (su, navn, lat, kat) in enumerate(data["dk_list"]):
            session.add(NfSpecies(liste="dk", sort=i, su=su == "*", navn=navn, latin=lat, kategori=kat,
                                  artnr=latin.get(lat.lower()) or names.get(navn.lower())))
        for i, (_, star, navn, lat, eng) in enumerate(data["vp_list"]):
            session.add(NfSpecies(liste="vp", sort=i, su=star == "*", navn=navn, latin=lat, engelsk=eng, artnr=latin.get(lat.lower())))
    if (await session.execute(select(NfFamily.id).limit(1))).first() is None:
        for orden, fam, eng, n in data["families"]:
            session.add(NfFamily(orden=orden, familie=fam, engelsk=eng, antal=int(n)))
    if (await session.execute(select(NfLink.id).limit(1))).first() is None:
        for i, (gruppe, titel, url) in enumerate(data["links"]):
            session.add(NfLink(gruppe=gruppe, titel=titel, url=url, sort=i))
    if (await session.execute(select(NfFeed.id).limit(1))).first() is None:
        for navn, site, land in data["feeds"]:
            session.add(NfFeed(navn=navn, site_url=site or None, land=land, active=bool(site)))
    if (await session.execute(select(NfArticle.id).limit(1))).first() is None:
        for a in data["articles"]:
            session.add(NfArticle(kind=a["kind"], title=a["title"], body=a["body"], writers=a["writers"] or None, tags=a["tags"],
                                  dato=date.fromisoformat(a["dato"]), source_url=a["source_url"]))
    await session.commit()


# ---------------------------------------------------------------- roller og rettigheder


@dataclass
class Ctx:
    user: User | None
    flags: dict[str, str]
    perms: set[str] = field(default_factory=set)
    roles: list[str] = field(default_factory=list)
    dofb_insider: bool = False

    @property
    def superadmin(self) -> bool:
        return bool(self.user) and self.user.role == SUPERADMIN

    @property
    def insider(self) -> bool:
        return bool(self.perms or self.roles)

    def on(self, key: str) -> bool:
        if key.startswith("dofbasen"):
            return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.dofb_insider)
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.insider)

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def can_edit(self, perm: str) -> bool:
        return self.can(perm) and self.on("netfugl_edit")

    def out(self) -> dict:
        feats = {k: self.on(k) for k in features.FEATURES if k.startswith("netfugl")}
        # Integration med DOFbasen-modulet kræver, at også det er synligt for brugeren
        feats["netfugl_dofbasen"] = feats["netfugl_dofbasen"] and self.on("dofbasen")
        return {"perms": sorted(self.perms), "roles": self.roles, "features": feats}


async def context(session: AsyncSession, user: User | None) -> Ctx:
    from portal.services import dofbasen

    ctx = Ctx(user=user, flags=await features.load(session))
    if user is None:
        return ctx
    if user.role == SUPERADMIN or can(user, "netfugl"):
        ctx.perms = set(PERMS)
    rows = (await session.execute(text("""
        SELECT r.key, r.perms FROM nf_user_roles ur JOIN nf_roles r ON r.key = ur.role_key WHERE ur.user_id = :u
    """), {"u": user.id})).all()
    for key, perms in rows:
        ctx.roles.append(key)
        ctx.perms |= set(perms or []) & set(PERMS)
    ctx.dofb_insider = (await dofbasen.context(session, user)).insider
    return ctx


async def audit(session: AsyncSession, user: User | None, action: str, target: str | None = None) -> None:
    session.add(NfAudit(user_id=user.id if user else None, action=action, target=(target or "")[:200]))


async def set_user_roles(session: AsyncSession, user_id: int, roles: list[str], by: User) -> None:
    valid = set((await session.execute(select(NfRole.key))).scalars())
    await session.execute(delete(NfUserRole).where(NfUserRole.user_id == user_id))
    for r in roles:
        if r in valid:
            session.add(NfUserRole(user_id=user_id, role_key=r, granted_by=by.id))


# ---------------------------------------------------------------- krydslister

# Listetyper: auto = beregnes fra DOFbasen; manual = brugerens egne kryds. {p} = år, måned eller område.
LISTS = {
    "dk": {"name": "Danske Arter", "auto": True, "ref": "dk", "manual": True},
    "aar": {"name": "Danske Årsarter {p}", "auto": True, "ref": "dk", "param": "year"},
    "md": {"name": "Danske Månedsarter {p}", "auto": True, "ref": "dk", "param": "month"},
    "afd": {"name": "Lokalarter: {p}", "auto": True, "ref": "dk", "param": "afdeling"},
    "kom": {"name": "Lokalarter: {p}", "auto": True, "ref": "dk", "param": "kommune"},
    "ring": {"name": "Danske Ringmærkningsarter", "auto": False, "ref": "dk", "manual": True},
    "aedel": {"name": "Danske Ædelarter", "auto": False, "ref": "dk", "manual": True},
    "aedelaar": {"name": "Danske Ædelårsarter {p}", "auto": False, "ref": "dk", "param": "year"},
    "vp": {"name": "VP Artsliste", "auto": False, "ref": "vp", "manual": True},
    "falskvp": {"name": "Falsk VP Artsliste", "auto": False, "ref": "vp", "manual": True},
}
MONTHS = ["Januar", "Februar", "Marts", "April", "Maj", "Juni", "Juli", "August", "September", "Oktober", "November", "December"]
KRYDS_VERSION = "nfkryds"


def parse_list(key: str) -> tuple[str, str | None]:
    """'aar:2026' → ('aar', '2026'). Fejl ved ukendt liste."""
    base, _, param = key.partition(":")
    if base not in LISTS:
        raise ValueError(f"Ukendt krydsliste {key}")
    spec = LISTS[base]
    if spec.get("param") and not param:
        raise ValueError(f"Krydslisten {base} kræver {spec['param']}")
    if spec.get("param") == "year" and not (param.isdigit() and 1900 < int(param) < 2100):
        raise ValueError("Ugyldigt år")
    if spec.get("param") == "month" and not (param.isdigit() and 1 <= int(param) <= 12):
        raise ValueError("Ugyldig måned")
    if spec.get("param") == "afdeling" and not param.isdigit():
        raise ValueError("Ugyldig afdeling")
    if spec.get("param") == "kommune" and not re.fullmatch(r"\d{3,4}", param):
        raise ValueError("Ugyldig kommune")
    return base, param or None


async def list_name(session: AsyncSession, key: str) -> str:
    base, p = parse_list(key)
    name = LISTS[base]["name"]
    if base == "md":
        p = MONTHS[int(p) - 1]
    elif base == "afd":
        p = (await session.execute(text("SELECT navn FROM afdelinger WHERE id = :i"), {"i": int(p)})).scalar() or p
    elif base == "kom":
        p = (await session.execute(text("SELECT navn FROM kommuner WHERE kode = :k"), {"k": p})).scalar() or p
    return name.format(p=p)


async def participants(session: AsyncSession) -> dict[str, str]:
    """obserkode → navn. 'brugere': portalens brugere, der viser deres lister. 'aktive': de mest aktive DOFbasen-observatører
    (demo – beregnes af workeren og gemmes i Valkey)."""
    st = await settings(session)
    rows = (await session.execute(text("""
        SELECT obserkode, coalesce(navn, obserkode) FROM users
        WHERE NOT blocked AND approved AND coalesce((prefs->>'lists_public')::boolean, true)
    """))).all()
    out = {r[0]: r[1] for r in rows}
    if st["participants"] == "aktive":
        extra = json.loads(await kv().get("nf:aktive") or "[]")
        for kode, navn in extra:
            out.setdefault(kode, navn)
    return out


async def refresh_active(session: AsyncSession) -> None:
    """De mest aktive observatører (flest arter i DK de seneste 3 år) til demo-tilstanden 'aktive'."""
    st = await settings(session)
    async with ObsSession() as obs:  # SET LOCAL skal ramme den database, der læser observationerne
        await obs.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
        rows = (await obs.execute(text(f"""
            SELECT o.obserkode, trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS navn
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN observers ob ON ob.obserkode = o.obserkode
            WHERE o.dato >= :d AND s.list_artnr IS NOT NULL AND o.antal > 0 AND o.hemmelig = 0
            GROUP BY o.obserkode, ob.fornavn, ob.efternavn ORDER BY count(DISTINCT s.list_artnr) DESC LIMIT {int(st['aktive_max'])}
        """), {"d": dk_today() - timedelta(days=3 * 365)})).all()
    await kv().set("nf:aktive", json.dumps([[r[0], r[1] or r[0]] for r in rows]))


async def refresh_auto(session: AsyncSession) -> int:
    """Genberegn de automatiske kryds for alle deltagere (første fund pr. art pr. liste). Returnerer antal rækker."""
    st = await settings(session)
    if st["participants"] == "aktive":
        await refresh_active(session)
    codes = sorted(await participants(session))
    await session.execute(text("DELETE FROM nf_auto WHERE NOT (obserkode = ANY(:c))"), {"c": codes})
    await session.commit()
    y0 = date(dk_today().year - int(st["year_lists"]) + 1, 1, 1)
    base = f"""FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
        WHERE o.obserkode = ANY(:codes) AND s.list_artnr IS NOT NULL AND NOT ls.excluded AND o.antal > 0
          AND o.dato <= current_date AND {countable_sql()}"""
    queries = [
        f"""SELECT DISTINCT ON (o.obserkode, s.list_artnr) o.obserkode, 'dk', s.list_artnr, o.dato, o.obsid, o.loknr {base}
            ORDER BY o.obserkode, s.list_artnr, o.dato, o.obsid""",
        f"""SELECT DISTINCT ON (o.obserkode, extract(year FROM o.dato), s.list_artnr) o.obserkode,
                   'aar:' || extract(year FROM o.dato)::int, s.list_artnr, o.dato, o.obsid, o.loknr {base} AND o.dato >= :y0
            ORDER BY o.obserkode, extract(year FROM o.dato), s.list_artnr, o.dato, o.obsid""",
        f"""SELECT DISTINCT ON (o.obserkode, extract(month FROM o.dato), s.list_artnr) o.obserkode,
                   'md:' || extract(month FROM o.dato)::int, s.list_artnr, o.dato, o.obsid, o.loknr {base}
            ORDER BY o.obserkode, extract(month FROM o.dato), s.list_artnr, o.dato, o.obsid""",
        f"""SELECT DISTINCT ON (o.obserkode, a.afd, s.list_artnr) o.obserkode, 'afd:' || a.afd, s.list_artnr, o.dato, o.obsid, o.loknr
            {base.replace('WHERE', 'CROSS JOIN LATERAL unnest(o.afdelinger) AS a(afd) WHERE', 1)}
            ORDER BY o.obserkode, a.afd, s.list_artnr, o.dato, o.obsid""",
        f"""SELECT DISTINCT ON (o.obserkode, l.kommune_kode, s.list_artnr) o.obserkode, 'kom:' || l.kommune_kode, s.list_artnr,
                   o.dato, o.obsid, o.loknr
            {base.replace('WHERE', 'JOIN locations l ON l.loknr = o.loknr WHERE l.kommune_kode IS NOT NULL AND', 1)}
            ORDER BY o.obserkode, l.kommune_kode, s.list_artnr, o.dato, o.obsid""",
    ]
    # Observationerne ligger i observationsgruppen og nf_auto i brugergruppen: en sætning må ikke blande dem, så krydsene
    # læses for sig og skrives samlet bagefter (én INSERT pr. portion via unnest)
    total = 0
    for i in range(0, len(codes), 25):
        batch = codes[i:i + 25]
        rows: list = []
        async with ObsSession() as obs:
            await obs.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
            for q in queries:
                rows += (await obs.execute(text(q), {"codes": batch, "y0": y0})).all()
        await session.execute(text("DELETE FROM nf_auto WHERE obserkode = ANY(:c)"), {"c": batch})
        if rows:
            cols = list(zip(*rows))
            await session.execute(text("""
                INSERT INTO nf_auto (obserkode, liste, artnr, dato, obsid, loknr)
                SELECT * FROM unnest(CAST(:k AS varchar[]), CAST(:l AS varchar[]), CAST(:a AS integer[]), CAST(:d AS date[]),
                                     CAST(:o AS bigint[]), CAST(:lok AS integer[]))
            """), {"k": list(cols[0]), "l": list(cols[1]), "a": list(cols[2]), "d": list(cols[3]), "o": list(cols[4]),
                   "lok": list(cols[5])})
        total += len(rows)
        await session.commit()
    await bump(KRYDS_VERSION)
    await kv().set("nf:auto:at", datetime.now(timezone.utc).isoformat())
    return total


async def load_kryds(session: AsyncSession, key: str) -> dict:
    """Alle deltageres kryds på en liste: {people: {kode: navn}, rows: [{kode, key, navn, latin, dato, lok, land, obsid, kilde}]}.
    key i rækkerne er artens nøgle: 'a<artnr>' for DK-lister, 'l<latin>' for VP-lister."""
    base, p = parse_list(key)

    async def produce() -> dict:
        people = await participants(session)
        codes = list(people)
        vp = LISTS[base]["ref"] == "vp"
        rows: list[dict] = []
        if LISTS[base]["auto"] or base == "vp":
            auto_list = "dk" if base == "vp" else key
            auto = (await session.execute(text("""
                SELECT a.obserkode, a.artnr, s.navn, s.latin, a.dato, a.obsid, a.loknr, l.navn AS lok
                FROM nf_auto a JOIN species s ON s.artnr = a.artnr LEFT JOIN locations l ON l.loknr = a.loknr
                WHERE a.liste = :l AND a.obserkode = ANY(:c)
            """), {"l": auto_list, "c": codes})).mappings().all()
            for r in auto:
                k = f"l{(r['latin'] or '').lower()}" if vp else f"a{r['artnr']}"
                rows.append({"kode": r["obserkode"], "key": k, "artnr": r["artnr"], "navn": r["navn"], "latin": r["latin"],
                             "dato": r["dato"].isoformat(), "lok": r["lok"], "land": "Danmark", "obsid": r["obsid"], "kilde": "dofbasen"})
        manual_list = {"aedelaar": "aedel"}.get(base, base)
        if LISTS[base].get("manual") or base == "aedelaar":
            q = "SELECT * FROM nf_kryds WHERE liste = :l AND obserkode = ANY(:c)"
            args = {"l": manual_list, "c": codes}
            if base == "aedelaar":
                q += " AND dato BETWEEN :a AND :b"
                args.update(a=date(int(p), 1, 1), b=date(int(p), 12, 31))
            man = (await session.execute(text(q), args)).mappings().all()
            hidden = {(m["obserkode"], f"a{m['artnr']}" if not vp and m["artnr"] else f"l{m['latin'].lower()}") for m in man if m["hidden"]}
            rows = [r for r in rows if (r["kode"], r["key"]) not in hidden]
            for m in man:
                if m["hidden"]:
                    continue
                k = f"a{m['artnr']}" if not vp and m["artnr"] else f"l{m['latin'].lower()}"
                rows.append({"kode": m["obserkode"], "key": k, "artnr": m["artnr"], "navn": m["navn"], "latin": m["latin"],
                             "dato": m["dato"].isoformat() if m["dato"] else None, "lok": m["lokalitet"], "land": m["land"],
                             "obsid": None, "kilde": "manuel", "id": m["id"], "note": m["note"]})
        # Én række pr. person og art – den tidligste
        best: dict[tuple[str, str], dict] = {}
        for r in rows:
            k = (r["kode"], r["key"])
            if k not in best or (r["dato"] or "9999") < (best[k]["dato"] or "9999"):
                best[k] = r
        return {"people": people, "rows": list(best.values()), "name": await list_name(session, key)}

    return await cached(f"nf:kryds:{key}:{await version(KRYDS_VERSION)}", 6 * 3600, produce)


def ranking(data: dict) -> list[dict]:
    per: dict[str, list[dict]] = {}
    for r in data["rows"]:
        per.setdefault(r["kode"], []).append(r)
    out = []
    for kode, rows in per.items():
        last = max(rows, key=lambda r: r["dato"] or "")
        out.append({"kode": kode, "navn": data["people"].get(kode, kode), "antal": len(rows),
                    "opdateret": last["dato"], "seneste": last["navn"], "seneste_land": last.get("land")})
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
    for r in data["rows"]:
        if r["dato"]:
            per.setdefault(r["kode"], []).append(r)
    out = []
    for kode, rows in per.items():
        rows.sort(key=lambda r: r["dato"])
        undated = sum(1 for r in data["rows"] if r["kode"] == kode and not r["dato"])
        for i, r in enumerate(rows, start=1):
            out.append({**r, "x": i + undated, "person": data["people"].get(kode, kode)})
    out = [r for r in out if r["x"] >= min_count]
    out.sort(key=lambda r: (r["dato"], r["x"]), reverse=True)
    return out[:n]


def who_has_what(data: dict, reference: list[dict]) -> dict:
    """Arter på referencelisten med antal personer, der har dem; ukendte arter på krydslisterne kommer til sidst."""
    count: dict[str, int] = {}
    names: dict[str, dict] = {}
    for r in data["rows"]:
        count[r["key"]] = count.get(r["key"], 0) + 1
        names.setdefault(r["key"], r)
    total = len({r["kode"] for r in data["rows"]})
    rows, seen = [], set()
    for s in reference:
        k = s["key"]
        seen.add(k)
        rows.append({"key": k, "navn": s["navn"], "latin": s["latin"], "su": s.get("su"), "antal": count.get(k, 0)})
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
    stat = []
    for kode in {r["kode"] for r in data["rows"]}:
        stat.append({"kode": kode, "navn": data["people"].get(kode, kode), "antal": sum(1 for r in data["rows"] if r["kode"] == kode),
                     "blockers": len(per.get(kode, []))})
    stat.sort(key=lambda r: (-r["blockers"], -r["antal"], r["navn"].lower()))
    return {"total": total, "limit": limit, "rows": rows, "stat": [s for s in stat if s["blockers"]]}


def tophits(data: dict, year: int) -> list[dict]:
    """De 50 arter, flest har krydset i året (og hvor mange der har dem i alt)."""
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


async def reference(session: AsyncSession, ref: str) -> list[dict]:
    """DK- eller VP-listen som referenceliste (rækkefølge og SU-markering)."""
    rows = (await session.execute(select(NfSpecies).where(NfSpecies.liste == ref).order_by(NfSpecies.sort))).scalars().all()
    out = []
    for s in rows:
        key = f"a{s.artnr}" if ref == "dk" and s.artnr else f"l{s.latin.lower()}"
        out.append({"key": key, "navn": s.navn, "latin": s.latin, "su": s.su, "kategori": s.kategori, "artnr": s.artnr, "id": s.id})
    return out


# ---------------------------------------------------------------- RSS (Netfugl live)

UA = "DOF.tool/1.0 (Netfugl-showcase; RSS hver time)"
ATOM = "{http://www.w3.org/2005/Atom}"


def _strip(s: str | None, n: int = 600) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def _date(s: str | None) -> datetime:
    if s:
        try:
            d = email.utils.parsedate_to_datetime(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 eller Atom → [{guid, title, url, summary, published_at}]."""
    if b"<!ENTITY" in data[:4000]:
        raise ValueError("Feeds med entiteter afvises")
    # Mange feeds bruger HTML-entiteter (&nbsp;, &aring; …), som XML ikke kender – omskriv dem til tegnkoder
    text_ = data.decode("utf-8", "replace")
    text_ = re.sub(r"&([A-Za-z][A-Za-z0-9]{1,30});", lambda m: m.group(0) if m.group(1) in ("amp", "lt", "gt", "quot", "apos")
                   else (f"&#{html.entities.name2codepoint[m.group(1)]};" if m.group(1) in html.entities.name2codepoint else m.group(0)), text_)
    root = ET.fromstring(text_.encode("utf-8"))
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        items.append({"guid": (it.findtext("guid") or link or it.findtext("title") or "")[:400],
                      "title": _strip(it.findtext("title"), 400) or "(uden titel)", "url": link[:600] or None,
                      "summary": _strip(it.findtext("description")), "published_at": _date(it.findtext("pubDate"))})
    for it in root.iter(f"{ATOM}entry"):
        link_el = next((l for l in it.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), None)
        link = link_el.get("href", "") if link_el is not None else ""
        items.append({"guid": (it.findtext(f"{ATOM}id") or link)[:400], "title": _strip(it.findtext(f"{ATOM}title"), 400) or "(uden titel)",
                      "url": link[:600] or None, "summary": _strip(it.findtext(f"{ATOM}summary") or it.findtext(f"{ATOM}content")),
                      "published_at": _date(it.findtext(f"{ATOM}published") or it.findtext(f"{ATOM}updated"))})
    return [i for i in items if i["guid"]]


def discover(page: str, base: str) -> str | None:
    """Feed-adressen fra en hjemmesides <link rel="alternate" type="application/rss+xml|atom+xml">."""
    for m in re.finditer(r"<link\b[^>]*>", page, re.I):
        tag = m.group(0)
        if re.search(r"rel=[\"']?alternate", tag, re.I) and re.search(r"application/(rss|atom)\+xml", tag, re.I):
            href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
            if href:
                return str(httpx.URL(base).join(html.unescape(href.group(1))))
    return None


def _public_host(url: str) -> None:
    """Kun offentlige adresser: RSS-adresser indtastes af admins og følger omdirigeringer – de må aldrig ramme portalens
    egne tjenester (postgres, valkey, api) eller andre interne adresser."""
    import ipaddress
    import socket

    u = httpx.URL(url)
    if u.scheme not in ("http", "https") or not u.host:
        raise ValueError("Kun http(s)-adresser")
    for info in socket.getaddrinfo(u.host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"Adressen {u.host} er ikke offentlig")


async def _check_request(request: httpx.Request) -> None:
    _public_host(str(request.url))


async def _get(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as r:
        r.raise_for_status()
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > 3_000_000:
                raise ValueError("Svaret er for stort")
        return bytes(buf)


GUESSES = ("feed/", "rss/", "rss.xml", "feed.xml", "atom.xml", "?feed=rss2")


async def _guess(client: httpx.AsyncClient, site: str) -> str | None:
    """Almindelige feed-stier (WordPress, Drupal, statiske sider), når siden ikke selv angiver en feed-adresse."""
    for path in GUESSES:
        url = str(httpx.URL(site if site.endswith("/") else site + "/").join(path))
        try:
            if parse_feed(await _get(client, url)):
                return url
        except Exception:  # noqa: BLE001 – prøv næste sti
            continue
    return None


async def fetch_feeds(session: AsyncSession, only: int | None = None) -> dict:
    """Hent alle aktive RSS-kilder (én ad gangen). Gemmer højst 200 indslag pr. kilde."""
    q = select(NfFeed).where(NfFeed.active)
    if only:
        q = select(NfFeed).where(NfFeed.id == only)
    feeds = (await session.execute(q)).scalars().all()
    ok = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, max_redirects=5, headers={"User-Agent": UA},
                                 event_hooks={"request": [_check_request]}) as client:
        for f in feeds:
            try:
                if not f.feed_url:
                    if not f.site_url:
                        raise ValueError("Mangler adresse")
                    page = (await _get(client, f.site_url)).decode("utf-8", "replace")
                    f.feed_url = discover(page, f.site_url) or await _guess(client, f.site_url)
                    if not f.feed_url:
                        raise ValueError("Fandt ingen RSS-adresse på siden – angiv den under Admin → RSS")
                items = parse_feed(await _get(client, f.feed_url))
                for it in items[:100]:
                    await session.execute(text("""
                        INSERT INTO nf_feed_items (feed_id, guid, title, url, summary, published_at)
                        VALUES (:f, :g, :t, :u, :s, :p) ON CONFLICT (feed_id, guid) DO UPDATE SET title = EXCLUDED.title
                    """), {"f": f.id, "g": it["guid"], "t": it["title"], "u": it["url"], "s": it["summary"],
                           "p": min(it["published_at"], datetime.now(timezone.utc))})
                await session.execute(text("""
                    DELETE FROM nf_feed_items WHERE feed_id = :f AND id NOT IN
                      (SELECT id FROM nf_feed_items WHERE feed_id = :f ORDER BY published_at DESC LIMIT 200)
                """), {"f": f.id})
                f.items = (await session.execute(text("SELECT count(*) FROM nf_feed_items WHERE feed_id = :f"), {"f": f.id})).scalar()
                f.last_error = None
                ok += 1
            except Exception as exc:  # noqa: BLE001 – én dårlig kilde må ikke stoppe resten
                f.last_error = str(exc)[:400]
                log.info("RSS %s: %s", f.navn, exc)
            f.last_fetch = datetime.now(timezone.utc)
            await session.commit()
    await bump("nfrss")
    return {"feeds": len(feeds), "ok": ok}


def media_root():
    from pathlib import Path

    root = Path(get_settings().media_dir) / "netfugl"
    root.mkdir(parents=True, exist_ok=True)
    return root
