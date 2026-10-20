"""Mobgang-generator: officielle og private presets (selve lydmiksningen sker i browseren).

Officielle presets ses af alle og vedligeholdes af dem med modulet mobgang_presets (redaktører og op som standard);
private presets ses kun af brugeren selv.
Modulet er lukket i ynglesæsonen (perioden redigeres af admin), og brugeren skal bestå en kort etiktest hver
3. måned. Begge dele håndhæves også for selve lydfilerne (se audio_allowed og middlewaren i main.py)."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_perm, require_user
from portal.cache import get_json, kv, set_json
from portal.config import get_settings
from portal.db import SessionLocal, get_session
from portal.models import MobPreset, Setting, User
from portal.services.auth import can
from portal.timeutil import dk_today

log = logging.getLogger("portal.mobgang")
router = APIRouter(prefix="/api/mobgang", tags=["mobgang"])

MAX_PRESETS = 100
OFFICIAL_MODULE = "mobgang_presets"

# ---------------------------------------------------------------- arter og optagelser
# Optagelser fra xeno-canto (XC-nummer, fil i web/audio/mobgang). Kataloget sendes til browseren med /status og
# bruges af middlewaren til at spærre den enkelte arts lydfiler. start = tændt i en ny, tom opsætning.

GROUPS = [("drosler", "Drosler, nattergale og stjerter"), ("sangere", "Sangere og løvsangere"),
          ("fluesnappere", "Fluesnappere"), ("vaerlinger", "Værlinger og finker"), ("andre", "Fuglekonger, mejser og krager")]


# Optager og licens for katalogets optagelser (aflæst på xeno-canto). Licenser med ND (NoDerivatives) må afspilles,
# men udelades af downloadede mix, da et mix er bearbejdet materiale.
CREDITS = {
    988584: ("Mats Olsson", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    747444: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    663553: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1075168: ("Romuald Mikusek", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    296007: ("Marco Dragonetti", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    916831: ("Lennart Jeppsson", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1039066: ("Aku Kalliomäki", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1168128: ("Stanislas Wroza", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1086047: ("András Schmidt", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    108276: ("Stuart Fisher", "https://creativecommons.org/licenses/by-nc-nd/2.5/"),
    1005619: ("Aku Kalliomäki", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1011627: ("Bodo Sonnenburg", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    1002087: ("brickegickel", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    575807: ("Stanislas Wroza", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    325508: ("Mikołaj Koss", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1042932: ("Lars Lachmann", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    758912: ("Jens Kirkeby", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1067896: ("Arend Wassink", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1054073: ("Jens Kirkeby", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1147096: ("Dave Guo", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    495372: ("Oscar Campbell", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    997377: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1173388: ("Walter Wallner", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1151364: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    921935: ("Lars Mogensen", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1151994: ("Magnus Wadstein", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    288094: ("Mikael Litsgård", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    918321: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    67246: ("Patrik Åberg", "https://creativecommons.org/licenses/by-nc-sa/3.0/"),
    181443: ("Jarek Matusiak", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    812536: ("Thomas Bergman", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    766258: ("Stanislas Wroza", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1165095: ("Zsombor Károlyi", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    687689: ("Hugo Wieleman", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    1047896: ("Lars Edenius", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    813347: ("Peter Boesman", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    1082575: ("Gustav Haapalahti", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
}


def _license(url: str) -> str:
    m = re.search(r"/licenses/([a-z-]+)/([\d.]+)/", url or "")
    return f"CC {m.group(1).upper()} {m.group(2)}" if m else ""


def _sp(id_: str, artnr: int | None, navn: str, latin: str, group: str, *recs: tuple[int, str], start: bool = False) -> dict:
    out = []
    for xc, f in recs:
        recordist, url = CREDITS.get(xc, (None, None))
        out.append({"xc": xc, "file": f, "recordist": recordist, "license": _license(url), "license_url": url,
                    "nd": "-nd" in (url or "")})
    return {"id": id_, "artnr": artnr, "navn": navn, "latin": latin, "group": group, "start": start, "recs": out}


SPECIES = [
    _sp("solsort", 11870, "Solsort", "Turdus merula", "drosler", (988584, "solsort-XC988584.mp3"), start=True),
    _sp("sangdrossel", 12000, "Sangdrossel", "Turdus philomelos", "drosler", (747444, "sangdrossel-XC747444.mp3")),
    _sp("vindrossel", 12010, "Vindrossel", "Turdus iliacus", "drosler", (663553, "vindrossel-XC663553.mp3"), start=True),
    _sp("ringdrossel", 11860, "Ringdrossel", "Turdus torquatus", "drosler", (1075168, "ringdrossel-XC1075168.mp3")),
    _sp("stendrossel", 11620, "Stendrossel", "Monticola saxatilis", "drosler", (296007, "stendrossel-XC296007.mp3")),
    _sp("nattergal", 11030, "Nattergal", "Luscinia luscinia", "drosler", (916831, "nattergal-XC916831.mp3")),
    _sp("blaahals", 11060, "Blåhals", "Luscinia svecica", "drosler", (1039066, "blaahals-XC1039066.mp3")),
    _sp("blaastjert", 11130, "Blåstjert", "Tarsiger cyanurus", "drosler", (1168128, "blaastjert-XC1168128.mp3")),
    _sp("cettisanger", 12200, "Cettisanger", "Cettia cetti", "sangere", (1086047, "cettisanger-XC1086047.mp3")),
    _sp("sivsanger", 12430, "Sivsanger", "Acrocephalus schoenobaenus", "sangere", (108276, "sivsanger-XC108276.mp3")),
    _sp("buskroersanger", 12480, "Buskrørsanger", "Acrocephalus dumetorum", "sangere", (1005619, "buskroersanger-XC1005619.mp3")),
    _sp("gulbug", 12590, "Gulbug", "Hippolais icterina", "sangere", (1011627, "gulbug-XC1011627.mp3"), start=True),
    _sp("iberisksanger", 12656, "Iberisk Sanger", "Curruca iberiae", "sangere", (1002087, "iberisk-sanger-XC1002087.mp3")),
    _sp("hoegesanger", 12730, "Høgesanger", "Curruca nisoria", "sangere", (575807, "hoegesanger-XC575807.mp3")),
    _sp("lundsanger", 12930, "Lundsanger", "Phylloscopus trochiloides", "sangere", (325508, "lundsanger-XC325508.mp3")),
    _sp("nordsanger", 12950, "Nordsanger", "Phylloscopus borealis", "sangere", (1042932, "nordsanger-XC1042932.mp3")),
    _sp("fuglekongesanger", 12980, "Fuglekongesanger", "Phylloscopus proregulus", "sangere", (758912, "fuglekongesanger-XC758912.mp3")),
    _sp("hvidbrynet", 13000, "Hvidbrynet Løvsanger", "Phylloscopus inornatus", "sangere",
        (1067896, "hvidbrynet-loevsanger-XC1067896.mp3"), start=True),
    _sp("himalayasanger", 13005, "Himalayasanger", "Phylloscopus humei", "sangere", (1054073, "himalayasanger-XC1054073.mp3")),
    _sp("schwarz", 13010, "Schwarz' Løvsanger", "Phylloscopus schwarzi", "sangere", (1147096, "schwarz-loevsanger-XC1147096.mp3")),
    _sp("brunloevsanger", 13030, "Brun Løvsanger", "Phylloscopus fuscatus", "sangere", (495372, "brun-loevsanger-XC495372.mp3")),
    _sp("skovsanger", 13080, "Skovsanger", "Phylloscopus sibilatrix", "sangere", (997377, "skovsanger-XC997377.mp3")),
    _sp("gransanger", 13110, "Gransanger", "Phylloscopus collybita", "sangere", (1173388, "gransanger-XC1173388.mp3")),
    _sp("loevsanger", 13120, "Løvsanger", "Phylloscopus trochilus", "sangere", (1151364, "loevsanger-XC1151364.mp3")),
    _sp("graafluesnapper", 13350, "Grå Fluesnapper", "Muscicapa striata", "fluesnappere",
        (921935, "graa-fluesnapper-XC921935.mp3"), start=True),
    _sp("lillefluesnapper", 13430, "Lille Fluesnapper", "Ficedula parva", "fluesnappere", (1151994, "lille-fluesnapper-XC1151994.mp3")),
    _sp("hvidhalset", 13480, "Hvidhalset Fluesnapper", "Ficedula albicollis", "fluesnappere",
        (288094, "hvidhalset-fluesnapper-XC288094.mp3")),
    _sp("brogetfluesnapper", 13490, "Broget Fluesnapper", "Ficedula hypoleuca", "fluesnappere",
        (918321, "broget-fluesnapper-XC918321.mp3")),
    _sp("karmindompap", 16790, "Karmindompap", "Carpodacus erythrinus", "vaerlinger", (67246, "karmindompap-XC67246.mp3")),
    _sp("hortulan", 18660, "Hortulan", "Emberiza hortulana", "vaerlinger", (181443, "hortulan-XC181443.mp3")),
    _sp("pilevaerling", 18730, "Pileværling", "Emberiza rustica", "vaerlinger", (812536, "pilevaerling-XC812536.mp3")),
    _sp("dvaergvaerling", 18740, "Dværgværling", "Emberiza pusilla", "vaerlinger", (766258, "dvaergvaerling-XC766258.mp3")),
    _sp("roedtoppet", 13150, "Rødtoppet Fuglekonge", "Regulus ignicapilla", "andre",
        (1165095, "roedtoppet-fuglekonge-XC1165095.mp3"), (687689, "roedtoppet-fuglekonge-XC687689.mp3"), start=True),
    _sp("fyrremejse", 14420, "Fyrremejse", "Poecile montanus", "andre", (1047896, "fyrremejse-XC1047896.mp3"), start=True),
    # Azurmejse findes ikke i DOFbasens artsliste – intet artsnummer
    _sp("azurmejse", None, "Azurmejse", "Cyanistes cyanus", "andre", (813347, "azurmejse-XC813347.mp3")),
    _sp("noeddekrige", 15570, "Nøddekrige", "Nucifraga caryocatactes", "andre", (1082575, "noeddekrige-XC1082575.mp3")),
]
SPECIES_IDS = [s["id"] for s in SPECIES]
FILE_SPECIES = {r["file"]: s["id"] for s in SPECIES for r in s["recs"]}

# ---------------------------------------------------------------- ynglesæson
# Hver art har sin egen lukkeperiode (dag og måned, gentages hvert år). Brugernes egne xeno-canto-optagelser af
# arter uden for kataloget følger "custom"-perioden. Den samlede lukning af hele modulet findes stadig, men er slået
# fra som standard.

SEASON_KEY = "mobgang_season"
PERIOD_DEFAULT = {"enabled": True, "from": "03-15", "to": "07-31"}  # MM-DD, begge dage inklusive
SEASON_DEFAULT = {"enabled": False, "from": "03-15", "to": "07-31"}


def _md(v: str) -> tuple[int, int]:
    m, d = (int(x) for x in v.split("-"))
    date(2024, m, d)  # skudår, så 29. februar er gyldig
    return m, d


def season_state(cfg: dict, today: date) -> dict:
    """Er perioden lukket i dag, og hvornår åbner/lukker den næste gang. Perioden må gå hen over nytår."""
    f, t = _md(cfg["from"]), _md(cfg["to"])
    cur = (today.month, today.day)
    inside = (f <= cur <= t) if f <= t else (cur >= f or cur <= t)
    closed = bool(cfg["enabled"]) and inside

    def next_day(md: tuple[int, int], after: date) -> date:
        for y in (after.year, after.year + 1, after.year + 2):
            try:
                d = date(y, *md)
            except ValueError:  # 29. februar i et ikke-skudår
                continue
            if d > after:
                return d
        return after

    return {"enabled": bool(cfg["enabled"]), "from": cfg["from"], "to": cfg["to"], "closed": closed,
            "opens": (next_day(t, today - timedelta(days=1)) + timedelta(days=1)).isoformat() if closed else None,
            "closes": next_day(f, today).isoformat() if cfg["enabled"] and not closed else None}


async def season(db: AsyncSession) -> dict:
    """Hele modulets lukning + hver arts lukning i dag."""
    row = await db.get(Setting, SEASON_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}
    today = dk_today()
    per = stored.get("species") or {}
    return {**season_state({**SEASON_DEFAULT, **{k: stored[k] for k in SEASON_DEFAULT if k in stored}}, today),
            "species": {sid: season_state({**PERIOD_DEFAULT, **(per.get(sid) or {})}, today) for sid in SPECIES_IDS},
            "custom": season_state({**PERIOD_DEFAULT, **(stored.get("custom") or {})}, today)}


class PeriodIn(BaseModel):
    enabled: bool
    from_: str = Field(alias="from", pattern=r"^\d{2}-\d{2}$")
    to: str = Field(pattern=r"^\d{2}-\d{2}$")

    @field_validator("from_", "to")
    @classmethod
    def _valid(cls, v: str) -> str:
        try:
            _md(v)
        except ValueError as exc:
            raise ValueError("Ugyldig dato") from exc
        return v

    def value(self) -> dict:
        return {"enabled": self.enabled, "from": self.from_, "to": self.to}


class SeasonIn(PeriodIn):
    species: dict[str, PeriodIn] = Field(default_factory=dict)
    custom: PeriodIn | None = None


@router.put("/season")
async def put_season(body: SeasonIn, user: User = Depends(require_perm("mobgang_season")), db: AsyncSession = Depends(get_session)):
    unknown = set(body.species) - set(SPECIES_IDS)
    if unknown:
        raise HTTPException(400, f"Ukendte arter: {', '.join(sorted(unknown))}")
    row = await db.get(Setting, SEASON_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}
    value = {**body.value(), "species": {**(stored.get("species") or {}), **{k: v.value() for k, v in body.species.items()}},
             "custom": body.custom.value() if body.custom else stored.get("custom") or PERIOD_DEFAULT}
    if row is None:
        db.add(Setting(key=SEASON_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return await season(db)


# ---------------------------------------------------------------- egne xeno-canto-optagelser
# Brugeren indsætter et link eller XC-nummer. Art, optager og licens læses af optagelsens side (xeno-canto's API
# kræver nøgle); arten kobles til portalens artsliste via det latinske navn. Lydfilen hentes én gang og gemmes
# lokalt, så etiktest og lukkeperiode også gælder egne optagelser.

XC_REF = re.compile(r"^\s*(?:(?:https?://)?(?:www\.)?xeno-canto\.org/|XC\s*)?(\d{1,9})(?:/download)?/?\s*$", re.IGNORECASE)
_XC_TITLE = re.compile(r"<title>XC(\d+)\s+(.*?)\s+\(([^)]+)\)\s*::", re.S)
_XC_REC = re.compile(r"class='jp-xc-recordist'>([^<]+)<")
_XC_LIC = re.compile(r"<h2>License</h2>\s*<p><a href='(https?://creativecommons\.org/[^']+)'>(?:<img[^>]*>)?\s*([^<]+)</a>")
XC_MAX_BYTES = 40 * 1024 * 1024
XC_TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
            "audio/ogg": ".ogg", "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/mp4": ".m4a"}


def parse_xc_ref(ref: str) -> int:
    m = XC_REF.match(ref or "")
    if not m:
        raise HTTPException(400, "Indsæt et xeno-canto-link (fx https://xeno-canto.org/988584) eller et XC-nummer")
    return int(m.group(1))


def parse_xc_page(page: str) -> dict:
    t = _XC_TITLE.search(page)
    if not t:
        raise ValueError("ukendt sideformat")
    rec, lic = _XC_REC.search(page), _XC_LIC.search(page)
    return {"en": html.unescape(t.group(2)).strip(), "latin": html.unescape(t.group(3)).strip(),
            "recordist": html.unescape(rec.group(1)).strip() if rec else None,
            "license": html.unescape(lic.group(2)).strip() if lic else None, "license_url": lic.group(1) if lic else None,
            "downloadable": "og:audio' content=" in page or f"/{t.group(1)}/download" in page}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
                             headers={"User-Agent": get_settings().dof_user_agent})


async def xc_meta(db: AsyncSession, nr: int) -> dict:
    """Optagelsens art, optager og licens (gemt i 30 dage) samt hvilken art i portalen og i kataloget den hører til."""
    key = f"mobgang:xc:{nr}"
    meta = await get_json(key)
    if meta is None:
        async with _client() as c:
            try:
                resp = await c.get(f"https://xeno-canto.org/{nr}")
            except httpx.HTTPError as exc:
                raise HTTPException(502, "xeno-canto svarer ikke lige nu – prøv igen senere") from exc
        if resp.status_code == 404:
            raise HTTPException(404, f"XC{nr} findes ikke på xeno-canto")
        if resp.status_code != 200:
            raise HTTPException(502, f"xeno-canto svarede {resp.status_code}")
        try:
            meta = {"xc": nr, **parse_xc_page(resp.text)}
        except ValueError as exc:
            raise HTTPException(404, f"Fandt ikke XC{nr} på xeno-canto") from exc
        row = (await db.execute(text("""
            SELECT artnr, trim(both '[]' from navn) FROM species WHERE lower(latin) = lower(:l)
            ORDER BY (art_type = 'art') DESC, artnr LIMIT 1
        """), {"l": meta["latin"]})).first()
        meta["artnr"], meta["navn"] = (row[0], row[1]) if row else (None, None)
        await set_json(key, meta, 30 * 86400)
    sid = next((s["id"] for s in SPECIES if meta.get("artnr") and s["artnr"] == meta["artnr"]), None)
    return {**meta, "sid": sid}


def xc_closed(st: dict, meta: dict) -> dict:
    """Lukkeperioden der gælder optagelsen: artens egen, hvis arten er i kataloget, ellers perioden for egne optagelser."""
    return st["species"][meta["sid"]] if meta.get("sid") else st["custom"]


class XcIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)


@router.post("/xc")
async def add_xc(body: XcIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    nr = parse_xc_ref(body.ref)
    if not await kv().set(f"mobgang:xcrl:{user.id}", "1", nx=True, ex=2):
        raise HTTPException(429, "Vent et øjeblik før næste opslag")
    meta = await xc_meta(db, nr)
    if not meta["downloadable"]:
        raise HTTPException(400, f"XC{nr} kan ikke downloades fra xeno-canto (fx en beskyttet art)")
    return {**meta, "season": xc_closed(await season(db), meta)}


def _xc_dir() -> Path:
    d = Path(get_settings().media_dir) / "xc"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _xc_file(nr: int) -> Path:
    """Lydfilen fra xeno-canto – hentes første gang og genbruges derefter (lås mod samtidige hentninger)."""
    d = _xc_dir()
    hit = next(d.glob(f"{nr}.*"), None)
    if hit:
        return hit
    r = kv()
    while not await r.set(f"mobgang:xcdl:{nr}", "1", nx=True, ex=120):
        await asyncio.sleep(0.5)
        if hit := next(d.glob(f"{nr}.*"), None):
            return hit
    try:
        async with _client() as c, c.stream("GET", f"https://xeno-canto.org/{nr}/download") as resp:
            if resp.status_code != 200:
                raise HTTPException(502, f"xeno-canto leverede ikke lydfilen ({resp.status_code})")
            ext = XC_TYPES.get(resp.headers.get("content-type", "").split(";")[0].strip().lower())
            if ext is None:
                raise HTTPException(502, "xeno-canto leverede ikke en lydfil")
            tmp = d / f"{nr}.part"
            size = 0
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > XC_MAX_BYTES:
                        f.close()
                        tmp.unlink(missing_ok=True)
                        raise HTTPException(413, "Optagelsen er for stor (højst 40 MB)")
                    f.write(chunk)
            target = d / f"{nr}{ext}"
            tmp.rename(target)
            return target
    except httpx.HTTPError as exc:
        raise HTTPException(502, "xeno-canto svarer ikke lige nu – prøv igen senere") from exc
    finally:
        await r.delete(f"mobgang:xcdl:{nr}")


@router.get("/xc/{nr}/audio")
async def xc_audio(nr: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not await kv().exists(f"mobgang:ethics:{user.id}"):
        raise HTTPException(403, "Mobgang kræver bestået etiktest")
    meta = await xc_meta(db, nr)
    st = await season(db)
    if st["closed"] or xc_closed(st, meta)["closed"]:
        raise HTTPException(403, "Arten er lukket i ynglesæsonen")
    if not meta["downloadable"]:
        raise HTTPException(400, "Optagelsen kan ikke downloades")
    path = await _xc_file(nr)
    return FileResponse(path, headers={"Cache-Control": "private, no-cache"})


# ---------------------------------------------------------------- etiktest

ETHICS_DAYS = 91  # skal bestås igen hver 3. måned
ETHICS_PREF = "mobgang_ethics_at"
QUESTIONS = [
    {"q": "Det er maj, og du hører en mulig sjælden sanger i et krat, hvor den kan yngle. Må du afspille mobgang eller sang for at få den frem?",
     "options": ["Ja, hvis det kun er kortvarigt", "Nej – playback over for mulige ynglefugle er uetisk",
                 "Ja, hvis fuglen er sjælden nok", "Kun tidligt om morgenen"],
     "answer": 1,
     "why": "Lyden kan stresse fuglene så meget, at de opgiver territoriet eller forlader reden. Derfor er modulet også lukket i ynglesæsonen."},
    {"q": "Du deltager i et officielt birdrace (fx Club300's Forårsbirdrace). Må du bruge mobgang eller andre optagelser til at få arter frem?",
     "options": ["Ja, det er en del af konkurrencen", "Kun til arter, der er svære at finde",
                 "Nej – det er hverken tilladt at bruge mobgang eller andre lydoptagelser", "Kun hvis alle hold gør det"],
     "answer": 2,
     "why": "Ved officielle birdraces er al brug af mobgang og lydoptagelser forbudt."},
    {"q": "Du overvejer at bruge mobgang uden for yngletiden. Hvad skal du først gøre?",
     "options": ["Skrue højt op, så lyden når langt ud", "Undersøge om der er andre fuglekiggere i området",
                 "Melde ud på sociale medier, at du bruger mobgang", "Ingenting – uden for yngletiden er det altid i orden"],
     "answer": 1,
     "why": "Kunstig afspilning kan narre andre til at tro, at de hører en sjælden fugl, og give falsk alarm."},
    {"q": "Fuglen viser sig kort, men du mangler det perfekte foto. Hvad gør du?",
     "options": ["Afspiller mobgang igen, til billedet er i hus", "Deler en video af, hvordan man lokker den frem",
                 "Lader mobilen blive i lommen og observerer fuglen på dens egne præmisser",
                 "Afspiller lyden kraftigere for at få den tættere på"],
     "answer": 2,
     "why": "Fuglens trivsel og energiforbrug er vigtigere end din registrering. Del heller ikke ukritisk videoer med mobgang – det kan få uerfarne til at kopiere adfærden."},
]


def _ethics(user: User) -> dict:
    at = (user.prefs or {}).get(ETHICS_PREF)
    until = (datetime.fromisoformat(at) + timedelta(days=ETHICS_DAYS)) if at else None
    return {"passed_at": at, "valid_until": until.isoformat() if until else None,
            "due": until is None or until <= datetime.now(timezone.utc)}


async def _remember(user: User, ethics: dict) -> None:
    """Lydfilerne tjekkes i middlewaren uden databaseopslag: et flag i Valkey der udløber med testen."""
    if not ethics["due"]:
        ttl = int((datetime.fromisoformat(ethics["valid_until"]) - datetime.now(timezone.utc)).total_seconds())
        await kv().set(f"mobgang:ethics:{user.id}", "1", ex=max(ttl, 1))


async def audio_allowed(user_id: int, path: str) -> bool:
    """Må brugeren hente lydfilen: gyldig etiktest, modulet er åbent, og artens periode er ikke lukket."""
    sid = FILE_SPECIES.get(path.rsplit("/", 1)[-1])
    if sid is None or not await kv().exists(f"mobgang:ethics:{user_id}"):
        return False
    async with SessionLocal() as db:
        st = await season(db)
    return not st["closed"] and not st["species"][sid]["closed"]


@router.get("/status")
async def status(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    ethics = _ethics(user)
    await _remember(user, ethics)
    return {"season": await season(db), "ethics": ethics, "species": SPECIES, "groups": GROUPS, "questions": [
        {"q": q["q"], "options": q["options"]} for q in QUESTIONS]}


class EthicsIn(BaseModel):
    answers: list[int] = Field(min_length=len(QUESTIONS), max_length=len(QUESTIONS))


@router.post("/ethics")
async def ethics(body: EthicsIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    results = [{"correct": a == q["answer"], "answer": q["answer"], "why": q["why"]} for a, q in zip(body.answers, QUESTIONS)]
    passed = all(r["correct"] for r in results)
    if passed:
        user.prefs = {**(user.prefs or {}), ETHICS_PREF: datetime.now(timezone.utc).isoformat()}
        await db.commit()
        await _remember(user, _ethics(user))
    return {"passed": passed, "results": results, "ethics": _ethics(user)}

# ---------------------------------------------------------------- presets


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]
    official: bool = False


def preset_out(p: MobPreset, user: User | None) -> dict:
    editable = can(user, OFFICIAL_MODULE) if p.official else bool(user and p.user_id == user.id)
    return {"id": p.id, "name": p.name, "config": p.config or {}, "official": p.official, "editable": editable,
            "updated_at": p.updated_at}


def _check(config: dict) -> None:
    # En chase gemmer en sammensætning pr. trin (højst 12)
    if len(str(config)) > 90000 or len(config.get("steps") or []) > 12:
        raise HTTPException(400, "Opsætningen er for stor")


def _may_official(user: User) -> None:
    if not can(user, OFFICIAL_MODULE):
        raise HTTPException(403, "Kræver adgang til Mobgang: officielle presets")


async def _editable(db: AsyncSession, user: User, preset_id: int) -> MobPreset:
    p = await db.get(MobPreset, preset_id)
    if p is None or (not p.official and p.user_id != user.id):
        raise HTTPException(404)
    if p.official:
        _may_official(user)
    return p


@router.get("/presets")
async def presets(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    """Officielle presets først, derefter brugerens egne."""
    cond = MobPreset.official if user is None else or_(MobPreset.official, MobPreset.user_id == user.id)
    rows = (await db.execute(select(MobPreset).where(cond).order_by(MobPreset.official.desc(), MobPreset.name))).scalars().all()
    return [preset_out(p, user) for p in rows]


@router.post("/presets")
async def create_preset(body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    if body.official:
        _may_official(user)
    else:
        n = (await db.execute(select(func.count()).select_from(MobPreset)
                              .where(MobPreset.user_id == user.id, ~MobPreset.official))).scalar_one()
        if n >= MAX_PRESETS:
            raise HTTPException(400, f"Du kan højst gemme {MAX_PRESETS} presets")
    p = MobPreset(user_id=user.id, name=body.name.strip(), config=body.config, official=body.official)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return preset_out(p, user)


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: int, body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    p = await _editable(db, user, preset_id)
    if body.official != p.official:
        _may_official(user)  # gøre et privat preset officielt eller omvendt
    p.name, p.config, p.official = body.name.strip(), body.config, body.official
    await db.commit()
    await db.refresh(p)
    return preset_out(p, user)


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.delete(await _editable(db, user, preset_id))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- officielle startpresets

ALL = tuple(SPECIES_IDS)


def _species(species: dict[str, int]) -> dict:
    return {s: {"on": s in species, "count": species.get(s, 1), "vol": 80} for s in ALL}


def _cfg(target: str = "", boost: int = 6, duck: int = -6, **species: int) -> dict:
    """species: art-id → antal individer (arter der ikke nævnes er slået fra)."""
    return {"v": 1, "master": 80, "target": target, "boost": boost, "duck": duck, "species": _species(species)}


def _chase(*steps: tuple, boost: int = 9, duck: int = -6, loop: bool = True) -> dict:
    """Chase: trin (navn, sekunder, target, {art-id: antal}[, fade]) – fade "in"/"out" fader trinnet ind fra eller ud
    til stilhed over hele trinnets længde. Første trin er også opsætningen uden chase."""
    first = steps[0]
    return {**_cfg(first[2], boost, duck, **first[3]), "loop": loop,
            "steps": [{"name": st[0], "secs": st[1], "target": st[2], "species": _species(st[3]), "fade": st[4] if len(st) > 4 else ""}
                      for st in steps]}


def _rotation(base: dict[str, int], targets: list[tuple[str, str]], secs: int) -> list[tuple]:
    """Toppen af en sæson-chase: flokken er fuld, og target skifter mellem sibiriske løvsangere – én ad gangen."""
    return [(name, secs, t, {**base, t: 1}) for t, name in targets]


SIBIRISKE_EFTERAAR = [("hvidbrynet", "Hvidbrynet Løvsanger"), ("fuglekongesanger", "Fuglekongesanger"),
                      ("himalayasanger", "Himalayasanger"), ("schwarz", "Schwarz' Løvsanger"), ("brunloevsanger", "Brun Løvsanger")]
SIBIRISKE_FORAAR = [("lundsanger", "Lundsanger"), ("nordsanger", "Nordsanger"), ("hvidbrynet", "Hvidbrynet Løvsanger"),
                    ("fuglekongesanger", "Fuglekongesanger")]


def _sig(cfg: dict) -> tuple:
    """Det hørbare indhold af en opsætning – til at se, om et officielt preset er uændret siden det blev oprettet."""
    on = tuple(sorted((k, v.get("count", 1), v.get("vol", 80)) for k, v in (cfg.get("species") or {}).items() if v.get("on")))
    return (cfg.get("target") or "", cfg.get("boost"), cfg.get("duck"), cfg.get("master"), on, bool(cfg.get("steps")))


# Hver version oprettes én gang; nye arter får en ny version, så redaktørernes rettelser i de gamle bevares
SEED = {
    "mobgang_seed_v1": [
        ("Alle arter – lige højt", _cfg(**{s: 1 for s in ("solsort", "fyrremejse", "hvidbrynet", "roedtoppet",
                                                 "vindrossel", "gulbug", "graafluesnapper")})),
        ("Hvidbrynet Løvsanger i mejseflokken", _cfg("hvidbrynet", 9, -6, fyrremejse=3, roedtoppet=2, hvidbrynet=1, solsort=1)),
        ("Rødtoppet Fuglekonge i mejseflokken", _cfg("roedtoppet", 9, -6, fyrremejse=3, roedtoppet=1, hvidbrynet=1)),
        ("Efterårstræk: Vindrossel og Solsort", _cfg("vindrossel", 6, -6, vindrossel=4, solsort=2)),
        ("Sommerkrattet: Gulbug", _cfg("gulbug", 6, -6, gulbug=1, graafluesnapper=1, solsort=2, fyrremejse=1)),
        ("Sommerkrattet: Grå Fluesnapper", _cfg("graafluesnapper", 9, -9, graafluesnapper=1, gulbug=1, solsort=2, fyrremejse=1)),
    ],
    "mobgang_seed_v2": [
        ("Fluesnappere: Lille Fluesnapper", _cfg("lillefluesnapper", 9, -6, lillefluesnapper=1, brogetfluesnapper=2,
                                                  graafluesnapper=1, hvidhalset=1, fyrremejse=1)),
        ("Fluesnappere: Hvidhalset blandt Brogede", _cfg("hvidhalset", 9, -6, hvidhalset=1, brogetfluesnapper=3, graafluesnapper=1)),
        ("Sibiriske løvsangere: Schwarz' Løvsanger", _cfg("schwarz", 9, -6, schwarz=1, brunloevsanger=1, hvidbrynet=1,
                                                          roedtoppet=2, fyrremejse=2)),
        ("Sibiriske løvsangere: Brun Løvsanger", _cfg("brunloevsanger", 9, -6, brunloevsanger=1, schwarz=1, hvidbrynet=1,
                                                      roedtoppet=2, fyrremejse=2)),
        ("Bøgeskoven: Lundsanger", _cfg("lundsanger", 9, -6, lundsanger=1, sangdrossel=1, solsort=1, brogetfluesnapper=1,
                                        graafluesnapper=1)),
        ("Krattet: Høgesanger", _cfg("hoegesanger", 9, -6, hoegesanger=1, gulbug=1, nattergal=1, solsort=1)),
        ("Natsang: Blåhals blandt Nattergale", _cfg("blaahals", 9, -6, blaahals=1, nattergal=2, cettisanger=1, sangdrossel=1)),
        ("Sjældne drosler: Blåstjert", _cfg("blaastjert", 9, -6, blaastjert=1, solsort=2, sangdrossel=1, roedtoppet=1, fyrremejse=1)),
        ("Sjældne drosler: Stendrossel", _cfg("stendrossel", 9, -6, stendrossel=1, solsort=1, sangdrossel=1, vindrossel=1)),
        ("Nåleskoven: Nøddekrige", _cfg("noeddekrige", 9, -6, noeddekrige=1, fyrremejse=2, roedtoppet=1, solsort=1)),
    ],
    "mobgang_seed_v3": [
        ("Sibiriske løvsangere: Fuglekongesanger", _cfg("fuglekongesanger", 9, -6, fuglekongesanger=1, hvidbrynet=1, gransanger=1,
                                                        roedtoppet=2, fyrremejse=1)),
        ("Sibiriske løvsangere: Himalayasanger blandt Hvidbrynede", _cfg("himalayasanger", 9, -9, himalayasanger=1, hvidbrynet=2,
                                                                          gransanger=1, roedtoppet=1)),
        ("Løvsangerflokken: Nordsanger", _cfg("nordsanger", 9, -6, nordsanger=1, loevsanger=2, gransanger=2, hvidbrynet=1)),
        ("Bøgeskoven: Skovsanger", _cfg("skovsanger", 6, -6, skovsanger=1, loevsanger=1, gransanger=1, brogetfluesnapper=1, solsort=1)),
        ("Krattet: Iberisk Sanger", _cfg("iberisksanger", 9, -6, iberisksanger=1, gulbug=1, loevsanger=1, gransanger=1)),
        ("Krattet: Karmindompap", _cfg("karmindompap", 6, -6, karmindompap=1, gulbug=1, loevsanger=1, hoegesanger=1)),
        ("Rørskoven: Buskrørsanger", _cfg("buskroersanger", 9, -6, buskroersanger=1, sivsanger=2, blaahals=1, nattergal=1)),
        ("Rørskoven: Sivsanger", _cfg("sivsanger", 6, -6, sivsanger=1, buskroersanger=1, cettisanger=1, blaahals=1)),
        ("Værlinger: Dværgværling", _cfg("dvaergvaerling", 9, -6, dvaergvaerling=1, pilevaerling=1, hortulan=1)),
        ("Værlinger: Pileværling", _cfg("pilevaerling", 9, -6, pilevaerling=1, dvaergvaerling=1, hortulan=1)),
        ("Marken: Hortulan", _cfg("hortulan", 6, -6, hortulan=1, karmindompap=1, solsort=1)),
        ("Mejseflokken: Azurmejse", _cfg("azurmejse", 9, -6, azurmejse=1, fyrremejse=3, roedtoppet=1)),
        ("Drosler: Ringdrossel", _cfg("ringdrossel", 9, -6, ringdrossel=1, solsort=2, sangdrossel=1, vindrossel=2)),
        ("Chase: Mejseflokken kommer forbi", _chase(
            ("Flokken nærmer sig", 45, "", {"fyrremejse": 3, "roedtoppet": 1}),
            ("Løvsangerne kommer med", 45, "", {"fyrremejse": 3, "roedtoppet": 2, "gransanger": 1, "hvidbrynet": 1}),
            ("Der er en ekstra!", 60, "fuglekongesanger", {"fyrremejse": 3, "roedtoppet": 2, "gransanger": 1, "hvidbrynet": 1,
                                                          "fuglekongesanger": 1}),
            ("Flokken trækker videre", 30, "", {"fyrremejse": 1, "roedtoppet": 1}))),
        ("Chase: Sibirisk løvsanger i flokken", _chase(
            ("Gransangere og Løvsangere", 40, "", {"gransanger": 2, "loevsanger": 1}),
            ("En Hvidbrynet melder sig", 40, "hvidbrynet", {"gransanger": 2, "loevsanger": 1, "hvidbrynet": 1}),
            ("Men hvad kalder dér?", 60, "himalayasanger", {"gransanger": 2, "loevsanger": 1, "hvidbrynet": 1, "himalayasanger": 1}),
            duck=-9)),
        ("Chase: Rørskoven ved daggry", _chase(
            ("Sivsangerne", 40, "", {"sivsanger": 2}),
            ("Blåhalsen går i gang", 40, "", {"sivsanger": 2, "blaahals": 1}),
            ("Buskrørsangeren", 60, "buskroersanger", {"sivsanger": 2, "blaahals": 1, "buskroersanger": 1}))),
    ],
}

# Sæson-chases på 7 minutter: fader ind fra stilhed, bygger op om skiftende targetarter, roterer mellem sibiriske
# løvsangere på toppen og fader ud igen.
SEED["mobgang_seed_v4"] = [
    ("Chase: Efterår – løvsangerflokken (7 min)", _chase(
        ("Stille skov", 40, "", {"gransanger": 1, "fyrremejse": 1}, "in"),
        ("Mejserne kommer", 40, "roedtoppet", {"fyrremejse": 2, "roedtoppet": 1, "gransanger": 1}),
        ("Flokken vokser", 40, "lillefluesnapper", {"fyrremejse": 3, "roedtoppet": 2, "gransanger": 2, "lillefluesnapper": 1,
                                                   "vindrossel": 1}),
        *_rotation({"fyrremejse": 3, "roedtoppet": 2, "gransanger": 2, "vindrossel": 1}, SIBIRISKE_EFTERAAR, 40),
        ("Flokken trækker videre", 40, "", {"fyrremejse": 2, "roedtoppet": 1, "gransanger": 1}),
        ("Stilheden vender tilbage", 60, "", {"fyrremejse": 1, "gransanger": 1}, "out"),
        loop=False)),
    ("Chase: Forår – sangerkrattet (7 min)", _chase(
        ("Daggry", 40, "", {"solsort": 1, "loevsanger": 1}, "in"),
        ("Løvsangerne", 40, "gransanger", {"loevsanger": 2, "gransanger": 1, "solsort": 1}),
        ("Krattet vågner", 40, "hoegesanger", {"loevsanger": 2, "gransanger": 1, "hoegesanger": 1, "nattergal": 1}),
        ("Karmindompappen synger", 40, "karmindompap", {"loevsanger": 2, "gransanger": 2, "hoegesanger": 1, "nattergal": 1,
                                                        "karmindompap": 1}),
        *_rotation({"loevsanger": 2, "gransanger": 2, "skovsanger": 1, "karmindompap": 1, "nattergal": 1}, SIBIRISKE_FORAAR, 40),
        ("Roen sænker sig", 40, "", {"loevsanger": 1, "gransanger": 1, "solsort": 1}),
        ("Formiddag", 60, "", {"solsort": 1, "loevsanger": 1}, "out"),
        loop=False)),
]

# Officielle presets, der opdateres med de nye arter – kun hvis en redaktør ikke har ændret dem siden oprettelsen
SEED_UPDATES = {
    "mobgang_seed_v3": {
        "Alle arter – lige højt": _cfg(**{s: 1 for s in ALL}),
        "Sibiriske løvsangere: Schwarz' Løvsanger": _cfg("schwarz", 9, -6, schwarz=1, brunloevsanger=1, hvidbrynet=1,
                                                         fuglekongesanger=1, roedtoppet=2, fyrremejse=2),
        "Sibiriske løvsangere: Brun Løvsanger": _cfg("brunloevsanger", 9, -6, brunloevsanger=1, schwarz=1, hvidbrynet=1,
                                                     gransanger=1, roedtoppet=2, fyrremejse=2),
        "Bøgeskoven: Lundsanger": _cfg("lundsanger", 9, -6, lundsanger=1, skovsanger=1, gransanger=1, sangdrossel=1, solsort=1,
                                       brogetfluesnapper=1, graafluesnapper=1),
        "Krattet: Høgesanger": _cfg("hoegesanger", 9, -6, hoegesanger=1, gulbug=1, karmindompap=1, nattergal=1, solsort=1),
        "Natsang: Blåhals blandt Nattergale": _cfg("blaahals", 9, -6, blaahals=1, nattergal=2, sivsanger=1, cettisanger=1, sangdrossel=1),
        "Sjældne drosler: Stendrossel": _cfg("stendrossel", 9, -6, stendrossel=1, ringdrossel=1, solsort=1, sangdrossel=1, vindrossel=1),
    },
}


async def seed_presets(session: AsyncSession) -> None:
    """Opret de officielle startpresets (redaktører kan derefter rette og slette dem). En version kan også opdatere
    tidligere officielle presets – men kun dem, der stadig lyder som da de blev oprettet."""
    original = {name: cfg for presets_ in SEED.values() for name, cfg in presets_}
    for key, presets_ in SEED.items():
        if await session.get(Setting, key) is not None:
            continue
        updated = []
        for name, cfg in SEED_UPDATES.get(key, {}).items():
            row = (await session.execute(select(MobPreset).where(MobPreset.official, MobPreset.name == name))).scalars().first()
            if row is not None and _sig(row.config or {}) == _sig(original[name]):
                row.config = cfg
                updated.append(name)
        for name, cfg in presets_:
            session.add(MobPreset(user_id=None, name=name, config=cfg, official=True))
        session.add(Setting(key=key, value={"n": len(presets_), "updated": updated}))
        await session.commit()
        log.info("Oprettede %s og opdaterede %s officielle mobgang-presets (%s)", len(presets_), len(updated), key)
