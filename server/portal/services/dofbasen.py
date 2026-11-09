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
from portal.models import DofbArea, DofbAudit, DofbFocus, DofbList, DofbNews, DofbPage, DofbRole, DofbUserRole, User
from portal.services import dataset, doftool_obs, features
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
    have = set((await session.execute(select(DofbArea.kind).distinct())).scalars())
    for kind, areas in data.get("areas", {}).items():
        if kind not in have:  # pr. slags: findes der storlokaliteter, skal IBA-områderne stadig lægges ind (og omvendt)
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


async def ungrantable(session: AsyncSession, own: set[str], *, user_id: int | None = None, roles: list[str] | None = None,
                      perms: list[str] | None = None, role_key: str | None = None) -> set[str]:
    """Rettigheder, den der handler, ikke selv har, men som handlingen ville give eller fjerne (tom mængde = i orden).

    Rettigheden «roller» må kun bruges til at give og fjerne roller, hvis rettigheder man selv har – ellers kunne en med
    «roller» give sig selv DOFbasen-administratorrollen (samme regel som modulerne under Admin → Brugere). Dækker: roller til
    en bruger (user_id + roles: både nye og fjernede tæller), en ny eller rettet rolle (perms) og den rolle, der rettes eller
    slettes (role_key: dens nuværende rettigheder)."""
    wanted: set[str] = set(perms or [])
    keys: set[str] = set()
    if user_id is not None:
        current = set((await session.execute(select(DofbUserRole.role_key).where(DofbUserRole.user_id == user_id))).scalars())
        keys |= set(roles or []) ^ current
    if role_key is not None:
        keys.add(role_key)
    if keys:
        for p in (await session.execute(select(DofbRole.perms).where(DofbRole.key.in_(keys)))).scalars():
            wanted |= set(p or [])
    return wanted - own


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
# Sortering efter en vilkårlig kolonne (Søg-siden): kolonnens udtryk i den ydre forespørgsel
SORT_COLUMNS = {"dato": "dato", "art": "sortering", "antal": "antal", "koen": "koen", "adf": "adfkode", "alder": "alderkode",
                "dragt": "dragtkode", "lokalitet": "lokalitet", "afdeling": "afdeling", "kommune": "kommune",
                "observator": "observator", "tid": "coalesce(obstidfra, turtidfra)", "kommentar": "nullif(fuglnoter, '')"}
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
    sort: str = Field("", pattern="^(|" + "|".join(SORT_COLUMNS) + ")$")  # vinder over sortering
    desc: bool = False
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


# IBA-områderne i søgeformularen: DOFbasens IBA-id og -navn på lokaliteterne (fra zip-importen i Admin → Lokaliteter)
IBA_SQL = """SELECT d.iba_id AS id, min(d.iba_navn) AS navn, count(*) AS lokaliteter FROM location_details d
             WHERE d.iba_id IS NOT NULL GROUP BY d.iba_id ORDER BY min(d.iba_navn)"""

# Importen kender ikke alle IBA-områder (fx Ertholmene): de resterende hentes fra DOFbasens områdedata (content.json) og
# får id = IBA_EXTRA_BASE + DOFbasens id, så de ikke støder ind i importens id'er. Et IBA, importen kender, vinder altid.
IBA_EXTRA_BASE = 1000
_iba_extra: dict[int, dict] | None = None


def iba_extra() -> dict[int, dict]:
    """{søge-id: {navn, loknr}} for DOFbasens IBA-områder fra områdedata (uden om lokalitetsimporten)."""
    global _iba_extra
    if _iba_extra is None:
        data = json.loads(CONTENT.read_text(encoding="utf-8")) if CONTENT.is_file() else {}
        _iba_extra = {IBA_EXTRA_BASE + a["id"]: {"navn": a["navn"], "loknr": list(a["loknr"])}
                      for a in data.get("areas", {}).get("iba", []) if a.get("loknr")}
    return _iba_extra


async def iba_options(session: AsyncSession) -> list[dict]:
    """IBA-områder til søgeformularen: lokalitetsimportens plus de af DOFbasens områder, importen ikke har."""
    rows = [dict(r) for r in (await session.execute(text(IBA_SQL))).mappings()]
    have = {r["navn"] for r in rows}
    rows += [{"id": i, "navn": a["navn"], "lokaliteter": len(a["loknr"])} for i, a in iba_extra().items() if a["navn"] not in have]
    return sorted(rows, key=lambda r: r["navn"])


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
    elif p.omraade == "iba" and p.iba in iba_extra():
        where.append("o.loknr = ANY(:iba_loks)")  # IBA kun kendt fra områdedata: dets lokaliteter sendes med som array
        args["iba_loks"] = iba_extra()[p.iba]["loknr"]
    elif p.omraade == "iba" and p.iba:
        # IBA er et niveau som kommune og lokalafdeling: lokaliteternes IBA fra lokalitetsimporten (location_details,
        # samme databasegruppe som observationerne)
        where.append("o.loknr IN (SELECT d.loknr FROM location_details d WHERE d.iba_id = :iba)")
        args["iba"] = p.iba
    elif p.omraade == "stor" and p.stor:
        # dofb_areas ligger i brugergruppen og observationerne i observationsgruppen: lokaliteterne slås op for sig
        # (resolve_area) og sendes med som et array – én sætning må ikke blande grupperne (se dbgroups)
        where.append("o.loknr = ANY(:area_loks)")
        args["area"] = p.stor

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


def _from(where: str) -> str:
    return FROM.format(interval=INTERVAL_JOIN if "fa.sortering" in where else "")
INTERVAL_JOIN = "JOIN species fa ON fa.artnr = :fra_art JOIN species ta ON ta.artnr = :til_art"

ROW_COLS = """
    o.obsid, o.turid, o.dato, o.artnr, ds.navn AS art, ds.latin, ds.klass, ds.sortering, o.kategori, o.antal, o.koen, o.adfkode,
    ct.text AS adfaerd, o.alderkode, o.dragtkode, o.obstidfra, o.obstidtil, o.fuglnoter, o.metode, o.kvalitet,
    o.loknr, l.navn AS lokalitet, l.afdeling_id, af.navn AS afdeling, l.kommune_kode, k.navn AS kommune,
    o.obserkode, trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observator, ob.by AS obser_by,
    t.turtidfra, t.turtidtil, t.turnoter, t.medobser, o.indtastning,
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


async def resolve_area(session: AsyncSession, args: dict[str, Any]) -> None:
    """Erstat områdets id (args['area']) med dets lokalitetsnumre (args['area_loks']), slået op i brugergruppen."""
    if "area" not in args:
        return
    loks = (await session.execute(text("SELECT loknrs FROM dofb_areas WHERE id = :area"), {"area": args.pop("area")})).scalar()
    args["area_loks"] = list(loks or [])


async def search(session: AsyncSession, p: SearchIn, viewer: str | None, *, csv: bool = False, su_badge: bool = False) -> dict:
    where, args, notes = search_sql(p, viewer)
    await resolve_area(session, args)
    args["sub_as_species"] = p.vis_underarter_som_arter
    frm = _from(where)
    await session.execute(text("SET LOCAL statement_timeout = '25s'"))
    # Postgres-containerens /dev/shm er lille (Dockers standard 64 MB); parallelle scanninger kan løbe tør for den
    await session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
    limit = MAX_CSV if csv else p.limit
    offset = 0 if csv else (p.page - 1) * p.limit
    order = SORTS[p.sortering]
    if p.sort:
        col = "sum_antal" if p.sort == "antal" and p.summering else SORT_COLUMNS[p.sort]
        order = f"{col} {'DESC' if p.desc else 'ASC'} NULLS LAST, " + order
    if p.biggestobs:
        order = ("sum_antal" if p.summering else "antal") + " DESC, " + order
        limit = min(limit, max(0, p.biggestobs - offset))
    if p.summering:
        # Summering pr. tur og art: én række pr. (tur, art) med samlet antal; første observation repræsenterer rækken
        inner = f"""
            SELECT DISTINCT ON (o.turid, ds.artnr) {ROW_COLS},
                   sum(o.antal) OVER (PARTITION BY o.turid, ds.artnr) AS sum_antal,
                   count(*) OVER (PARTITION BY o.turid, ds.artnr) AS n_obs,
                   min(o.obstidfra) OVER (PARTITION BY o.turid, ds.artnr) AS g_obstidfra,
                   max(coalesce(o.obstidtil, o.obstidfra)) OVER (PARTITION BY o.turid, ds.artnr) AS g_obstidtil
            {frm} {ROW_JOINS} WHERE {where} ORDER BY o.turid, ds.artnr, o.obsid
        """
        count_sql = f"SELECT count(*) FROM (SELECT 1 {frm} WHERE {where} GROUP BY o.turid, o.artnr LIMIT 50001) c"
    else:
        # Underforespørgslen trækkes op af planlæggeren, så sortering og LIMIT stadig kan bruge indekserne
        inner = f"SELECT {ROW_COLS} {frm} {ROW_JOINS} WHERE {where}"
        count_sql = f"SELECT count(*) FROM (SELECT 1 {frm} WHERE {where} LIMIT 50001) c"
    # Hele datasættet: DOF.tool-gruppens træffere (samme SQL der) flettes ind i DOFbasen-forespørgslens yderste lag,
    # så sortering og sideinddeling gælder begge kilder under ét (services/dataset.py)
    tool, cols = await dataset.tool_fetch(f"SELECT * FROM ({inner}) x ORDER BY {order} LIMIT :tool_lim",
                                          {**args, "tool_lim": dataset.MAX_ROWS})
    params = {**args, "lim": limit, "off": offset}
    if tool:
        part, extra = dataset.union_part(tool, cols)
        params.update(extra)
        sql = f"SELECT * FROM (({inner}) UNION ALL ({part})) x ORDER BY {order} LIMIT :lim OFFSET :off"
    else:
        sql = f"SELECT * FROM ({inner}) x ORDER BY {order} LIMIT :lim OFFSET :off"
    rows = (await session.execute(text(sql), params)).mappings().all()
    out = [_row(r, p.summering) for r in rows]
    if csv:
        return {"rows": out, "notes": notes}
    await attach_media(out, session, su_badge)  # DKU-status, fotos og lyd som i Observationer
    total = ((await session.execute(text(count_sql), {k: v for k, v in args.items() if k != "sub_as_species"})).scalar() or 0) + len(tool)
    if p.biggestobs:
        total = min(total, p.biggestobs)
    d1, d2 = args.get("d1"), args.get("d2")
    return {"rows": out, "total": total, "capped": total > 50000, "page": p.page, "limit": p.limit,
            "period": [d1.isoformat() if d1 else None, d2.isoformat() if d2 else None], "notes": notes}


MAX_POINTS = 5000


async def search_points(session: AsyncSession, p: SearchIn, viewer: str | None) -> dict:
    """Alle søgningens observationer til et kort: én prik pr. position (fuglens eller lokalitetens) med antal observationer."""
    where, args, notes = search_sql(p, viewer)
    await resolve_area(session, args)
    frm = _from(where)
    await session.execute(text("SET LOCAL statement_timeout = '25s'"))
    await session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
    sql = f"""
        SELECT coalesce(o.obs_lat, l.lat) AS lat, coalesce(o.obs_lon, l.lon) AS lon, bool_or(o.obs_lat IS NOT NULL) AS praecis,
               min(o.loknr) AS loknr, min(l.navn) AS lokalitet, count(*) AS n,
               CASE WHEN bool_or(o.kategori = 'SU') THEN 'SU' WHEN bool_or(o.kategori = 'SUB') THEN 'SUB'
                    WHEN bool_or(o.kategori = 'bemaerk') THEN 'bemaerk' END AS kategori,
               (array_agg(DISTINCT s.navn))[1:6] AS arter
        {frm} WHERE {where} AND coalesce(o.obs_lat, l.lat) IS NOT NULL
        GROUP BY 1, 2 ORDER BY n DESC LIMIT :lim
    """
    rows = [dict(r) for r in (await session.execute(text(sql), {**args, "lim": MAX_POINTS + 1})).mappings().all()]
    tool, _ = await dataset.tool_fetch(sql, {**args, "lim": MAX_POINTS + 1})  # DOF.tool's prikker (services/dataset.py)
    rows = sorted(rows + tool, key=lambda r: -r["n"])
    return {"points": rows[:MAX_POINTS], "capped": len(rows) > MAX_POINTS, "notes": notes}


def _row(r, summed: bool) -> dict:
    d = dict(r)
    d["dato"] = d["dato"].isoformat()
    if summed:
        d["antal"] = d.pop("sum_antal")
        d["n_obs"] = d.pop("n_obs")
        # den summerede række viser hele tidsrummet for observationerne, ikke kun den første
        d["obstidfra"], d["obstidtil"] = d.pop("g_obstidfra"), d.pop("g_obstidtil")
    return d


# Indtastningsmetoden som tekst (CSV og observationens side)
INDTASTNING = {"dofbasen": "DOFbasen", "doftool": "DOF.tool", "manuel": "Manuelt fund"}

CSV_COLUMNS = ["Dato", "Turtidfra", "Turtidtil", "Loknr", "Loknavn", "Artnr", "Artnavn", "Latin", "Antal", "Koen",
               "Adfkode", "Adfbeskrivelse", "Alderkode", "Dragtkode", "Obserkode", "Navn", "Obser_by", "Medobser", "Turnoter",
               "Fuglnoter", "Metode", "Obstidfra", "Obstidtil", "Kvalitet", "Turid", "Obsid", "DOF_afdeling", "Kommune",
               "breddegrad", "laengdegrad", "Indtastning"]


def csv_line(r: dict) -> list:
    return [r["dato"], r["turtidfra"], r["turtidtil"], r["loknr"], r["lokalitet"], f"{r['artnr']:05d}", r["art"], r["latin"],
            r["antal"], r["koen"], r["adfkode"], r["adfaerd"], r["alderkode"], r["dragtkode"], r["obserkode"], r["observator"],
            r["obser_by"], r["medobser"], r["turnoter"], r["fuglnoter"], r["metode"], r["obstidfra"], r["obstidtil"], r["kvalitet"],
            r["turid"], r["obsid"], r["afdeling"], r["kommune"], r["lat"], r["lon"], INDTASTNING.get(r.get("indtastning") or "dofbasen", r.get("indtastning"))]




# ---------------------------------------------------------------- dagens observationer (DOFbasens og Dagslistens liste)

VISNING_RE = "^(fugle|allfugle|sufugle|trkfugle|patdyr|somfugle|krybdyr)$"
VISNING = {"fugle": "o.kategori <> 'alm'", "allfugle": "TRUE", "sufugle": "o.kategori IN ('SU', 'SUB')", "trkfugle": ADF[2][1]}


# Fænologi og lokale hits (som i feedet, se services/feed.py): joinene ph og lc ligger altid på dagslisten
PHENO_SQL = ("(s.art_type IN ('art', 'underart') AND ph.artnr IS NOT NULL AND (CASE WHEN ph.start_doy <= ph.end_doy "
             "THEN (:doy < ph.start_doy OR :doy > ph.end_doy) ELSE (:doy > ph.end_doy AND :doy < ph.start_doy) END))")
LOCAL_COND = "(coalesce(lc.n_finds, 0) < :lmax AND s.art_type IN ('art', 'underart'))"
MATRIX_LEVELS = ("SU", "SUB", "bemaerk", "all", "pheno", "local")
LOCAL_MAX = 10  # lokale hits: færre end så mange estimerede fund af arten på lokaliteten (som i feedet)


def parse_matrix(raw: str) -> dict[int, list[str]]:
    """«3:SU.SUB,5:all» → {3: ["SU", "SUB"], 5: ["all"]} (afdeling → markerede niveauer). Ukendte niveauer ignoreres."""
    out: dict[int, list[str]] = {}
    for part in raw.split(","):
        afd, _, lv = part.partition(":")
        if afd.isdigit():
            levels = [x for x in lv.split(".") if x in MATRIX_LEVELS]
            if levels:
                out[int(afd)] = levels
    return out


def _matrix_where(matrix: dict[int, list[str]], d: date, args: dict[str, Any]) -> str:
    """Afdeling × niveau-matrix: en observation vises, hvis dens afdeling har mindst ét markeret niveau, den opfylder.
    Fænologi og Lokale hits som i feedet (se services/feed.py), men uden SU/SUB-tilføjelsen."""
    # Hvert felt rummer kun sig selv: SUB er kun SUB (ikke SU), Alle er de almindelige observationer, og Fænologi og Lokale hits
    # gælder uanset kategori
    cond = {"SU": "o.kategori = 'SU'", "SUB": "o.kategori = 'SUB'", "bemaerk": "o.kategori = 'bemaerk'", "all": "o.kategori = 'alm'",
            "local": LOCAL_COND, "pheno": PHENO_SQL}
    parts = []
    for i, (afd, levels) in enumerate(sorted(matrix.items())):
        args[f"mx{i}"] = afd
        parts.append(f"(l.afdeling_id = :mx{i} AND ({' OR '.join(f'({cond[lv]})' for lv in levels)}))")
    return f"({' OR '.join(parts)})" if parts else "FALSE"


async def day_observations(db: AsyncSession, me: str | None, d: date, visning: str, lok: int | None, lokalitet: int | None, summering: str,
                           su_badge: bool = False, matrix: dict[int, list[str]] | None = None) -> dict:
    """Observationer på en dag: visning, afdeling (lok), én lokalitet og summering (tur/dato/ingen). Hemmelige vises kun for observatøren selv."""
    if visning in ("patdyr", "somfugle", "krybdyr"):
        return {"dato": d.isoformat(), "rows": [], "note": "Portalen henter kun fugle fra DOFbasen – andre dyr findes ikke i data."}
    from portal.services import seasons

    # Fugle – bemærkelsesværdige: bemærk-kategorierne og arter uden for deres normale periode (fænologi)
    where = [f"(o.kategori <> 'alm' OR {PHENO_SQL})" if visning == "fugle" else VISNING[visning], "o.dato = :d", "(o.hemmelig = 0 OR o.obserkode = :me)"]
    args: dict[str, Any] = {"d": d, "me": me, "doy": seasons.doy365(d), "lmax": LOCAL_MAX}
    if matrix is not None:
        # Matricen erstatter visning og afdeling
        where[0] = _matrix_where(matrix, d, args)
    elif lok:
        where.append("l.afdeling_id = :lok")
        args["lok"] = lok
    if lokalitet:
        where.append("o.loknr = :loknr")
        args["loknr"] = lokalitet
    group = {"tur": "o.turid, o.artnr", "dato": "o.obserkode, o.loknr, o.artnr", "ingen": "o.obsid"}[summering]
    sql = f"""
        SELECT min(o.obsid) AS obsid, array_agg(o.obsid) AS obsids, count(*) AS n_obs, sum(o.antal) AS antal, o.artnr, s.navn AS art, s.latin, s.sortering,
               min(o.kategori) FILTER (WHERE o.kategori <> 'alm') AS kat, o.loknr, l.navn AS lokalitet, l.afdeling_id,
               o.obserkode, max(trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, ''))) AS observator,
               string_agg(DISTINCT o.adfkode, ', ') AS adfkode, string_agg(DISTINCT o.alderkode, ', ') AS alder,
               string_agg(DISTINCT o.koen, ', ') AS koen, string_agg(DISTINCT o.dragtkode, ', ') AS dragt,
               bool_or(coalesce(o.fuglnoter, '') <> '') AS kommentar, bool_or(coalesce(t.turnoter, '') <> '') AS turkommentar,
               left(string_agg(DISTINCT nullif(o.fuglnoter, ''), ' · '), 600) AS obsnote, left(max(nullif(t.turnoter, '')), 600) AS turnote,
               bool_or(coalesce(t.medobser, '') <> '') AS medobser, left(max(nullif(t.medobser, '')), 300) AS medobsnote, bool_or(o.obs_lat IS NOT NULL) AS position,
               -- tidsrummet: observationens eget (fra første start til sidste tidspunkt) og turens – se obslist.js
               min(o.obstidfra) AS obstidfra, max(coalesce(o.obstidtil, o.obstidfra)) AS obstidtil,
               min(t.turtidfra) AS turtidfra, max(coalesce(t.turtidtil, t.turtidfra)) AS turtidtil,
               bool_or(o.obstidfra IS NOT NULL OR o.obstidtil IS NOT NULL OR t.turtidfra IS NOT NULL) AS tid,  -- DOFbasen-modulets ur
               max(st.status) AS dku, min(o.indtastning) AS indtastning,
               bool_or({PHENO_SQL}) AS pheno, bool_or({LOCAL_COND}) AS local, min(coalesce(lc.n_finds, 0)) AS lok_fund
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN trips t ON t.turid = o.turid LEFT JOIN observers ob ON ob.obserkode = o.obserkode
        LEFT JOIN dofb_obs_status st ON st.obsid = o.obsid
        LEFT JOIN species_season ph ON ph.artnr = coalesce(s.list_artnr, s.artnr)
        LEFT JOIN loc_species_finds lc ON lc.loknr = o.loknr AND lc.artnr = o.artnr
        WHERE {' AND '.join(where)}
        GROUP BY {group}, s.navn, s.latin, s.sortering, o.loknr, l.navn, l.afdeling_id, o.obserkode, o.artnr
        ORDER BY l.afdeling_id, s.sortering, l.navn LIMIT 10000
    """
    rows = [dict(r) for r in (await db.execute(text(sql), args)).mappings().all()]
    # DOF.tool-gruppens observationer samme dag (samme forespørgsel der) – grupperne går aldrig på tværs af kilderne
    # DOF.tool-gruppen har ikke fænologi- og fundtællingstabellerne (de beregnes af DOFbasens observationer): ingen fænologi,
    # og ingen lokale hits (n_finds sat højt)
    tool_sql = (sql.replace("LEFT JOIN species_season ph ON ph.artnr = coalesce(s.list_artnr, s.artnr)",
                            "LEFT JOIN (SELECT 0 AS artnr, 0 AS start_doy, 0 AS end_doy WHERE FALSE) ph ON ph.artnr = s.artnr")
                .replace("LEFT JOIN loc_species_finds lc ON lc.loknr = o.loknr AND lc.artnr = o.artnr",
                         "LEFT JOIN (SELECT 0 AS loknr, 0 AS artnr, 0 AS n_finds WHERE FALSE) lc ON lc.loknr = o.loknr AND lc.artnr = o.artnr")
                .replace("coalesce(lc.n_finds, 0)", "999999"))
    tool, _ = await dataset.tool_fetch(tool_sql, args)
    if tool:
        rows = sorted(rows + tool, key=lambda r: (r["afdeling_id"] is None, r["afdeling_id"] or 0, r["sortering"] or 0, r["lokalitet"] or ""))
    await attach_media(rows, db, su_badge, d)
    return {"dato": d.isoformat(), "rows": rows, "summering": summering}


def dku_kind(text: str) -> str:
    """DOFbasens DKU-statustekst → behandling / dku / su (farven på mærket)."""
    t = (text or "").lower()
    return "behandling" if "behandling" in t else "su" if "su" in t.split() or "sjældenhedsudvalg" in t else "dku"


async def obs_marks(db: AsyncSession, obsids: list[int], su_badge: bool, keys: list[tuple] = ()) -> tuple[set[int], set[tuple]]:
    """(observationer med en åben DKU-sag i portalen, fund SU har godkendt som (dato, artnr, loknr)). To opslag i
    brugergruppen – adskilt fra observationsopslagene, så der aldrig joines på tværs af grupperne."""
    if db is None:
        return set(), set()
    cases = set((await db.execute(text("""SELECT obsid FROM dku_cases WHERE source = 'dofbasen' AND status <> 'afgjort'
        AND obsid = ANY(CAST(:ids AS bigint[]))"""), {"ids": obsids})).scalars()) if obsids else set()
    su_ok: set[tuple] = set()
    keys = [k for k in dict.fromkeys(keys) if k[0] and k[1] and k[2]]
    if su_badge and keys:
        rows = (await db.execute(text("""SELECT a.dato, a.artnr, a.loknr FROM su_obs_approvals a
            JOIN unnest(CAST(:d AS date[]), CAST(:a AS int[]), CAST(:l AS int[])) AS k(dato, artnr, loknr)
              ON k.dato = a.dato AND k.artnr = a.artnr AND k.loknr = a.loknr
            WHERE a.decision = 'godkendt'"""), {"d": [_as_date(k[0]) for k in keys], "a": [k[1] for k in keys], "l": [k[2] for k in keys]})).all()
        su_ok = {(r[0].isoformat(), r[1], r[2]) for r in rows}
    return cases, su_ok


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def find_key(dato, artnr, loknr) -> tuple:
    """Fundets nøgle (dato som ISO-tekst, artnr, loknr) – som portalens tråde."""
    return (dato.isoformat() if isinstance(dato, date) else str(dato) if dato else None, artnr, loknr)


async def attach_media(rows: list[dict], db: AsyncSession | None = None, su_badge: bool = False, dato: date | None = None) -> None:
    """DOFbasens DKU-status, fotos og lyd fra medie-scannerens cache (som på tråden). Rækkens obsids fjernes bagefter.
    Mangler cachen, bruges portalens egen DKU-status (dofb_obs_status) som tekst. Med db markeres også åbne DKU-sager
    i portalen (dku_kind «behandling» = lyseblåt mærke) og SU's godkendelser (su_ok = gyldent SU-mærke)."""
    from portal.dof.jobs import media_many

    ids = {id(r): r.pop("obsids", None) or [r["obsid"]] for r in rows}
    every = [o for v in ids.values() for o in v]
    found = await media_many(every)
    key = {id(r): find_key(r.get("dato") or dato, r.get("artnr"), r.get("loknr")) for r in rows}
    cases, su_ok = await obs_marks(db, every, su_badge, list(key.values()))
    # DOF.tool's fund: fotos og lyd ligger hos indtastningen (field_reports.media) – ikke i DOFbasens mediecache
    tool_ids = [-o - doftool_obs.BASE for o in every if doftool_obs.is_doftool(o)]
    if tool_ids and db is not None:
        for rid, media in (await db.execute(text("SELECT id, media FROM field_reports WHERE id = ANY(:i)"), {"i": tool_ids})).all():
            m = list(media or [])
            found[doftool_obs.obsid_of(rid)] = {"images": [x["url"] for x in m if x.get("kind", "image") == "image"],
                                                "sounds": [x["url"] for x in m if x.get("kind") == "audio"], "dku": ""}
    own = {"behandling": "Til behandling hos DKU", "dku": "Behandlet af DKU", "su": "Godkendt af SU"}
    for r in rows:
        mine = ids[id(r)]
        media = [found[o] for o in mine if o in found]
        text_ = next((m["dku"] for m in media if m.get("dku")), "") or own.get(r.get("dku") or "", "")
        if any(o in cases for o in mine) and not text_:
            text_ = "DKU-sag i portalen"
        r["dku_status"] = text_
        r["dku_kind"] = "behandling" if any(o in cases for o in mine) else (dku_kind(text_) if text_ else None)
        r["foto"] = any(m.get("images") for m in media)
        r["lyd"] = any(m.get("sounds") for m in media)
        r["su_ok"] = key[id(r)] in su_ok
