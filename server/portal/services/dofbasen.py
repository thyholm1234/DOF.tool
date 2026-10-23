"""DOFbasen-modulet: en showcase af en ny udgave af dofbasen.dk på portalens data.

* Indhold (tekstsider, nyheder, fokus, opslagslister, storlokaliteter/IBA) ligger i dofb_*-tabellerne og lægges ind fra
  seed/dofbasen/content.json første gang. Teksterne er kopieret fra dofbasen.dk som eksempel (DOF har ophavsretten).
* Modulet har sine egne roller (dofb_roles) med rettigheder (PERMS). Superadmin og brugere med portal-modulet
  "dofbasen" har alle rettigheder.
* Søgningen (search_sql) er fælles for søgesiden, CSV-udtræk og det offentlige API.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.config import SEED_DIR
from portal.models import (DofbArea, DofbAudit, DofbFocus, DofbList, DofbNews, DofbPage, DofbRole, DofbUserRole, User)
from portal.services import features
from portal.services.auth import SUPERADMIN, can
from portal.timeutil import dk_today

CONTENT = SEED_DIR / "dofbasen" / "content.json"

PERMS = {
    "sider": "Tekstsider og forsidebokse",
    "nyheder": "Nyheder og fokus",
    "lister": "Opslagslister, links, storlokaliteter og IBA",
    "dku": "Kvalitetssikring (DKU): sæt observationer til behandling",
    "api": "API: tildel og fjern brugeres adgang, se tokens",
    "roller": "DOFbasen-roller: giv og fjern roller",
}
DEFAULT_ROLES = [
    ("admin", "DOFbasen-administrator", list(PERMS)),
    ("redaktoer", "Redaktør", ["sider", "nyheder"]),
    ("listeansvarlig", "Listeansvarlig", ["lister"]),
    ("dku", "DKU-medlem", ["dku"]),
    ("api", "API-ansvarlig", ["api"]),
]


# ---------------------------------------------------------------- seed


async def seed(session: AsyncSession) -> None:
    """Læg standardindhold og -roller ind, hvis de mangler (eksisterende rækker røres ikke)."""
    if (await session.execute(select(DofbRole.key).limit(1))).first() is None:
        for i, (key, name, perms) in enumerate(DEFAULT_ROLES):
            session.add(DofbRole(key=key, name=name, perms=perms, sort=i * 10))
    if not CONTENT.is_file():
        await session.commit()
        return
    data = json.loads(CONTENT.read_text(encoding="utf-8"))
    have = set((await session.execute(select(DofbPage.slug))).scalars())
    for slug, page in data["pages"].items():
        if slug not in have:
            session.add(DofbPage(slug=slug, title=page["title"], sections=page["sections"]))
    have = set((await session.execute(select(DofbList.key))).scalars())
    for key, lst in data["lists"].items():
        if key not in have:
            session.add(DofbList(key=key, title=lst["title"], intro=lst.get("intro", ""), columns=lst["columns"], rows=lst["rows"]))
    if (await session.execute(select(DofbNews.id).limit(1))).first() is None:
        for n in data["news"]:
            session.add(DofbNews(dato=date.fromisoformat(n["dato"]), title=n["title"], body=n["body"], pinned=bool(n.get("pinned"))))
    if (await session.execute(select(DofbFocus.id).limit(1))).first() is None:
        for i, f in enumerate(data["focus"]):
            session.add(DofbFocus(title=f["title"], body=f["body"], active=f.get("active", True), sort=i * 10))
    if (await session.execute(select(DofbArea.id).limit(1))).first() is None:
        for kind, areas in data.get("areas", {}).items():
            for a in areas:
                session.add(DofbArea(kind=kind, dof_id=a["id"], navn=a["navn"], loknrs=a["loknr"]))
    await session.commit()


# ---------------------------------------------------------------- roller og rettigheder


@dataclass
class Ctx:
    """Hvad den aktuelle bruger må i DOFbasen-modulet."""

    user: User | None
    flags: dict[str, str]
    perms: set[str] = field(default_factory=set)
    roles: list[str] = field(default_factory=list)

    @property
    def superadmin(self) -> bool:
        return bool(self.user) and self.user.role == SUPERADMIN

    @property
    def insider(self) -> bool:
        """Har en DOFbasen-rolle (eller er administrator for modulet) – ser forhåndsvisninger."""
        return bool(self.perms or self.roles)

    def on(self, key: str) -> bool:
        return features.visible(self.flags, key, superadmin=self.superadmin, insider=self.insider)

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def can_edit(self, perm: str) -> bool:
        """Redigering direkte på siderne (kræver også funktionen dofbasen_edit)."""
        return self.can(perm) and self.on("dofbasen_edit")

    def out(self) -> dict:
        return {"perms": sorted(self.perms), "roles": self.roles, "features": {k: self.on(k) for k in features.FEATURES}}


async def context(session: AsyncSession, user: User | None) -> Ctx:
    flags = await features.load(session)
    ctx = Ctx(user=user, flags=flags)
    if user is None:
        return ctx
    if user.role == SUPERADMIN or can(user, "dofbasen"):
        ctx.perms = set(PERMS)
    rows = (await session.execute(text("""
        SELECT r.key, r.perms FROM dofb_user_roles ur JOIN dofb_roles r ON r.key = ur.role_key WHERE ur.user_id = :u
    """), {"u": user.id})).all()
    for key, perms in rows:
        ctx.roles.append(key)
        ctx.perms |= set(perms or []) & set(PERMS)
    return ctx


async def audit(session: AsyncSession, user: User | None, action: str, target: str | None = None) -> None:
    session.add(DofbAudit(user_id=user.id if user else None, action=action, target=(target or "")[:200]))


async def set_user_roles(session: AsyncSession, user_id: int, roles: list[str], by: User) -> None:
    valid = set((await session.execute(select(DofbRole.key))).scalars())
    await session.execute(delete(DofbUserRole).where(DofbUserRole.user_id == user_id))
    for r in roles:
        if r in valid:
            session.add(DofbUserRole(user_id=user_id, role_key=r, granted_by=by.id))


# ---------------------------------------------------------------- API-tokens

TOKEN_PREFIX = "dofb_"


def new_token(prefix: str = TOKEN_PREFIX) -> tuple[str, str, str]:
    """(token, prefix til visning, sha256). Netfugl-modulet bruger samme funktion med prefikset 'nf_'."""
    token = prefix + secrets.token_urlsafe(32)
    return token, token[:12], hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- søgning

# Adfærd (søgesidens "adf"/linkenes "behaviour") → betingelse på adfærdskoden
_DIRS = "T|TF|TI|TU|N|NNØ|NØ|ØNØ|Ø|ØSØ|SØ|SSØ|S|SSV|SV|VSV|V|VNV|NV|NNV"
ADF = {
    1: ("Rastende", "(o.adfkode = 'R' OR o.adfkode LIKE 'R %')"),
    2: ("Trækkende", f"o.adfkode ~ '^({_DIRS})( |$)'"),
    3: ("Fouragerende", "(o.adfkode ~ '^FU( |$)' OR o.adfkode ~ ' FU$')"),
    5: ("Overflyvende", "o.adfkode LIKE 'OF%'"),
    6: ("Ynglefugle", "o.adfkode LIKE 'YF%'"),
    7: ("Indtrækkende", "o.adfkode ~ '(^| )TI$'"),
    8: ("Udtrækkende", "o.adfkode ~ '(^| )TU$'"),
    9: ("Ringmærket", "o.adfkode LIKE 'RI%'"),
    12: ("Død", "o.adfkode LIKE '%DØD'"),
}
ALDER = ["1K", "1K+", "2K", "2K+", "3K", "3K+", "4K", "4K+", "5K", "5K+", "AD", "IMM", "JUV", "PUL", "SUB"]
DRAGT = {"AFV": "Afvigende dragt", "EKL": "Eklipsedragt (ænder)", "BR": "Hun-farvet", "LF": "Lys form", "ML": "Mellemform",
         "MF": "Mørk form", "ODR": "Overgangsdragt", "PRA": "Pragtdragt", "SDR": "Sommerdragt/yngledragt", "VDR": "Vinterdragt"}
METODER = {2: "Totaloptælling", 3: "Ynglefugletælling", 4: "Trækfugletælling", 5: "Delvis optælling",
           6: "Optælling af artsgruppe (specificeret)", 7: "Udeladelse af artsgruppe (specificeret)", 8: "Punkttælling",
           9: "IBA-tælling", 10: "Havfugletælling", 11: "Rovfugletælling", 12: "Rastefugletælling", 13: "Diverse obs",
           15: "Indlandstræktælling", 16: "Ringmærkning", 17: "Træktælling, standardtid", 18: "Vandfugletælling",
           19: "Linjetaksering", 20: "Passiv lydoptagelse, fx NocMig", 21: "Havtælling, standardtid",
           22: "Indlandstælling, standardtid", 23: "FiNN - Random walk", 24: "Ilanddrevne døde fugle langs kyst",
           98: "Spontantælling", 99: "FiNN-tælling"}
SPECIES_GROUPS = {"alle": "Fugle - alle", "weird": "Fugle - bemærkelsesværdige", "faeno": "Fugle - fænologisk usædvanlige",
                  "rare": "Fugle - sjældne", "su": "Fugle - SU-arter"}
# Sortering på søgningens kolonner (ydre forespørgsel, se search)
SORTS = {"dato": "dato DESC, sortering, obsid", "art": "sortering, dato DESC, obsid",
         "lokalitet": "lokalitet, dato DESC, sortering", "observat": "observator, dato DESC, sortering"}
MAX_PAGE = 500
MAX_CSV = 50_000
MAX_DAYS = 3660  # én søgning dækker højst ti år, medmindre der er valgt art, lokalitet, område eller observatør


class SearchIn(BaseModel):
    """Søgeformularens felter (samme navne som dofbasen.dk/search, så links kan oversættes direkte)."""

    periode: str = Field("antaldage", pattern="^(none|dato|mellemdato|antaldage|maanedaar)$")
    dato: date | None = None
    dato_first: date | None = None
    dato_second: date | None = None
    dage: int = Field(7, ge=1, le=366)
    aar: int | None = Field(None, ge=1900, le=2100)
    maaned: int | None = Field(None, ge=1, le=12)
    dag: int | None = Field(None, ge=1, le=31)
    species: str = Field("alle", pattern="^(alle|weird|faeno|rare|su)$")
    omraade: str = Field("none", pattern="^(none|lokalitet|lokal|kommune|iba|stor)$")
    lok: int | None = None
    lokal: int | None = None
    kommune: str | None = Field(None, max_length=4)
    iba: int | None = None
    stor: int | None = None
    artdata: str = Field("keine", pattern="^(keine|art|artinterval)$")
    art: int | None = None
    fra_art: int | None = None
    til_art: int | None = None
    antalregl: str = Field("", pattern="^(|stoerre|mindre|ligmed)$")
    antal: int | None = Field(None, ge=0)
    adf: int | None = None
    alder: str = Field("", max_length=4)
    dragt: str = Field("", max_length=4)
    sex: str = Field("", pattern="^(|M|F)$")
    metode: int | None = None
    obserdata: str = Field("", max_length=16)
    medobser: bool = False
    koordinat: bool = False
    kommentar: bool = False
    udelad_burfugle: bool = False
    udelad_hybrider_ubestemte: bool = False
    vis_underarter_som_arter: bool = False
    summering: bool = False
    biggestobs: int | None = Field(None, ge=1, le=1000)
    textsearch: str = Field("", max_length=80)
    sortering: str = Field("dato", pattern="^(dato|art|lokalitet|observat)$")
    page: int = Field(1, ge=1, le=10_000)
    limit: int = Field(100, ge=1, le=MAX_PAGE)

    @field_validator("obserdata")
    @classmethod
    def _kode(cls, v: str) -> str:
        return v.strip().upper()


def period(p: SearchIn) -> tuple[date | None, date | None]:
    """Søgningens datointerval (begge ender med). None = ingen grænse i den ende."""
    today = dk_today()
    if p.periode == "dato":
        d = p.dato or today
        return d, d
    if p.periode == "mellemdato":
        a, b = p.dato_first or today - timedelta(days=7), p.dato_second or today
        return (a, b) if a <= b else (b, a)
    if p.periode == "antaldage":
        return today - timedelta(days=p.dage - 1), today
    if p.periode == "maanedaar":
        y = p.aar or today.year
        if p.maaned and p.dag:
            try:
                d = date(y, p.maaned, p.dag)
            except ValueError:
                d = date(y, p.maaned, 1)
            return d, d
        if p.maaned:
            nxt = date(y + (p.maaned == 12), p.maaned % 12 + 1, 1)
            return date(y, p.maaned, 1), nxt - timedelta(days=1)
        return date(y, 1, 1), date(y, 12, 31)
    return None, None


def search_sql(p: SearchIn, viewer: str | None) -> tuple[str, dict[str, Any], list[str]]:
    """WHERE-delen af en søgning, parametre og forklarende noter (fx når perioden er indsnævret)."""
    where, args, notes = ["(o.hemmelig = 0 OR o.obserkode = :viewer)"], {"viewer": viewer or "-"}, []
    a, b = period(p)
    narrow = p.omraade in ("lokalitet", "iba", "stor") or p.art or p.obserdata
    if a is None and not narrow:
        a, b = dk_today() - timedelta(days=6), dk_today()
        notes.append("Uden periode, art, lokalitet eller observatør søges de seneste 7 dage.")
    if a is not None and b is not None and (b - a).days > MAX_DAYS and not narrow:
        a = b - timedelta(days=MAX_DAYS)
        notes.append("Perioden er begrænset til ti år – vælg art, lokalitet eller observatør for at søge længere tilbage.")
    if a is not None:
        where.append("o.dato >= :d1")
        args["d1"] = a
    if b is not None:
        where.append("o.dato <= :d2")
        args["d2"] = b

    if p.species == "weird":
        where.append("o.kategori <> 'alm'")
    elif p.species == "faeno":
        where.append("o.kategori = 'bemaerk' AND EXISTS (SELECT 1 FROM phenology_windows pw WHERE pw.artnavn = s.navn)")
    elif p.species == "rare":
        where.append("o.kategori IN ('SU', 'SUB')")
    elif p.species == "su":
        where.append("o.kategori = 'SU'")

    if p.omraade == "lokalitet" and p.lok:
        where.append("o.loknr = :lok")
        args["lok"] = p.lok
    elif p.omraade == "lokal" and p.lokal:
        where.append("l.afdeling_id = :lokal")
        args["lokal"] = p.lokal
    elif p.omraade == "kommune" and p.kommune:
        where.append("l.kommune_kode = :kommune")
        args["kommune"] = p.kommune
    elif p.omraade in ("iba", "stor") and (p.iba if p.omraade == "iba" else p.stor):
        where.append("o.loknr = ANY(SELECT unnest(loknrs) FROM dofb_areas WHERE id = :area)")
        args["area"] = p.iba if p.omraade == "iba" else p.stor

    if p.artdata == "art" and p.art:
        if p.vis_underarter_som_arter:
            where.append("(o.artnr = :art OR (s.art_type = 'underart' AND s.list_artnr = :art))")
        else:
            where.append("o.artnr = :art")
        args["art"] = p.art
    elif p.artdata == "artinterval" and p.fra_art and p.til_art:
        where.append("s.sortering BETWEEN least(fa.sortering, ta.sortering) AND greatest(fa.sortering, ta.sortering)")
        args["fra_art"], args["til_art"] = p.fra_art, p.til_art

    if p.antal is not None and p.antalregl:
        where.append({"stoerre": "o.antal >= :antal", "mindre": "o.antal <= :antal", "ligmed": "o.antal = :antal"}[p.antalregl])
        args["antal"] = p.antal
    if p.adf in ADF:
        where.append(ADF[p.adf][1])
    if p.alder:
        where.append("o.alderkode = :alder")
        args["alder"] = p.alder
    if p.dragt:
        where.append("o.dragtkode = :dragt")
        args["dragt"] = p.dragt
    if p.sex:
        where.append("o.koen ILIKE :sex")
        args["sex"] = f"%{p.sex}%"
    if p.metode in METODER:
        where.append("o.metode = :metode")
        args["metode"] = METODER[p.metode]
    if p.obserdata:
        if p.medobser:
            where.append("(o.obserkode = :obser OR t.medobser ILIKE :obser_like)")
            args["obser_like"] = f"%{p.obserdata}%"
        else:
            where.append("o.obserkode = :obser")
        args["obser"] = p.obserdata
    if p.koordinat:
        where.append("o.obs_lat IS NOT NULL")
    if p.kommentar:
        where.append("coalesce(o.fuglnoter, '') <> ''")
    if p.udelad_burfugle:
        where.append("s.navn NOT LIKE '[%'")
    if p.udelad_hybrider_ubestemte:
        where.append("s.art_type NOT IN ('hybrid', 'ub')")
    if p.textsearch.strip():
        where.append("(o.fuglnoter ILIKE :txt OR t.turnoter ILIKE :txt)")
        args["txt"] = "%" + re.sub(r"([%_\\])", r"\\\1", p.textsearch.strip()) + "%"
    return " AND ".join(where), args, notes


FROM = """
    FROM observations o
    JOIN species s ON s.artnr = o.artnr
    LEFT JOIN locations l ON l.loknr = o.loknr
    LEFT JOIN trips t ON t.turid = o.turid
    {interval}
"""
INTERVAL_JOIN = "JOIN species fa ON fa.artnr = :fra_art JOIN species ta ON ta.artnr = :til_art"

ROW_COLS = """
    o.obsid, o.turid, o.dato, o.artnr, ds.navn AS art, ds.latin, ds.klass, ds.sortering, o.kategori, o.antal, o.koen, o.adfkode,
    ct.text AS adfaerd, o.alderkode, o.dragtkode, o.obstidfra, o.obstidtil, o.fuglnoter, o.metode, o.kvalitet,
    o.loknr, l.navn AS lokalitet, l.afdeling_id, af.navn AS afdeling, l.kommune_kode, k.navn AS kommune,
    o.obserkode, trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observator, ob.by AS obser_by,
    t.turtidfra, t.turtidtil, t.turnoter, t.medobser,
    coalesce(o.obs_lat, l.lat) AS lat, coalesce(o.obs_lon, l.lon) AS lon, (o.obs_lat IS NOT NULL) AS praecis,
    st.status AS dku
"""
ROW_JOINS = """
    JOIN species ds ON ds.artnr = CASE WHEN :sub_as_species AND s.art_type = 'underart' THEN coalesce(s.list_artnr, s.artnr) ELSE s.artnr END
    LEFT JOIN observers ob ON ob.obserkode = o.obserkode
    LEFT JOIN afdelinger af ON af.id = l.afdeling_id
    LEFT JOIN kommuner k ON k.kode = l.kommune_kode
    LEFT JOIN code_texts ct ON ct.kind = 'adf' AND ct.code = o.adfkode
    LEFT JOIN dofb_obs_status st ON st.obsid = o.obsid
"""


async def search(session: AsyncSession, p: SearchIn, viewer: str | None, *, csv: bool = False) -> dict:
    where, args, notes = search_sql(p, viewer)
    args["sub_as_species"] = p.vis_underarter_som_arter
    frm = FROM.format(interval=INTERVAL_JOIN if "fa.sortering" in where else "")
    await session.execute(text("SET LOCAL statement_timeout = '25s'"))
    # Postgres-containerens /dev/shm er lille (Dockers standard 64 MB); parallelle scanninger kan løbe tør for den
    await session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
    limit = MAX_CSV if csv else p.limit
    offset = 0 if csv else (p.page - 1) * p.limit
    order = SORTS[p.sortering]
    if p.biggestobs:
        order = ("sum_antal" if p.summering else "antal") + " DESC, " + order
        limit = min(limit, max(0, p.biggestobs - offset))
    if p.summering:
        # Summering pr. tur og art: én række pr. (tur, art) med samlet antal; første observation repræsenterer rækken
        inner = f"""
            SELECT DISTINCT ON (o.turid, ds.artnr) {ROW_COLS},
                   sum(o.antal) OVER (PARTITION BY o.turid, ds.artnr) AS sum_antal,
                   count(*) OVER (PARTITION BY o.turid, ds.artnr) AS n_obs
            {frm} {ROW_JOINS} WHERE {where} ORDER BY o.turid, ds.artnr, o.obsid
        """
        count_sql = f"SELECT count(*) FROM (SELECT 1 {frm} WHERE {where} GROUP BY o.turid, o.artnr LIMIT 50001) c"
    else:
        # Underforespørgslen trækkes op af planlæggeren, så sortering og LIMIT stadig kan bruge indekserne
        inner = f"SELECT {ROW_COLS} {frm} {ROW_JOINS} WHERE {where}"
        count_sql = f"SELECT count(*) FROM (SELECT 1 {frm} WHERE {where} LIMIT 50001) c"
    sql = f"SELECT * FROM ({inner}) x ORDER BY {order} LIMIT :lim OFFSET :off"
    rows = (await session.execute(text(sql), {**args, "lim": limit, "off": offset})).mappings().all()
    out = [_row(r, p.summering) for r in rows]
    if csv:
        return {"rows": out, "notes": notes}
    total = (await session.execute(text(count_sql), {k: v for k, v in args.items() if k != "sub_as_species"})).scalar() or 0
    if p.biggestobs:
        total = min(total, p.biggestobs)
    d1, d2 = args.get("d1"), args.get("d2")
    return {"rows": out, "total": total, "capped": total > 50000, "page": p.page, "limit": p.limit,
            "period": [d1.isoformat() if d1 else None, d2.isoformat() if d2 else None], "notes": notes}


def _row(r, summed: bool) -> dict:
    d = dict(r)
    d["dato"] = d["dato"].isoformat()
    if summed:
        d["antal"] = d.pop("sum_antal")
        d["n_obs"] = d.pop("n_obs")
    return d


CSV_COLUMNS = ["Dato", "Turtidfra", "Turtidtil", "Loknr", "Loknavn", "Artnr", "Artnavn", "Latin", "Antal", "Koen",
               "Adfkode", "Adfbeskrivelse", "Alderkode", "Dragtkode", "Obserkode", "Navn", "Obser_by", "Medobser", "Turnoter",
               "Fuglnoter", "Metode", "Obstidfra", "Obstidtil", "Kvalitet", "Turid", "Obsid", "DOF_afdeling", "Kommune",
               "breddegrad", "laengdegrad"]


def csv_line(r: dict) -> list:
    return [r["dato"], r["turtidfra"], r["turtidtil"], r["loknr"], r["lokalitet"], f"{r['artnr']:05d}", r["art"], r["latin"],
            r["antal"], r["koen"], r["adfkode"], r["adfaerd"], r["alderkode"], r["dragtkode"], r["obserkode"], r["observator"],
            r["obser_by"], r["medobser"], r["turnoter"], r["fuglnoter"], r["metode"], r["obstidfra"], r["obstidtil"], r["kvalitet"],
            r["turid"], r["obsid"], r["afdeling"], r["kommune"], r["lat"], r["lon"]]
