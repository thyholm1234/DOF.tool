"""Mobgang-generator: officielle og private presets (selve lydmiksningen sker i browseren).

Officielle presets ses af alle og vedligeholdes af redaktører og op; private presets ses kun af brugeren selv.
Modulet er lukket i ynglesæsonen (perioden redigeres af admin), og brugeren skal bestå en kort etiktest hver
3. måned. Begge dele håndhæves også for selve lydfilerne (se audio_allowed og middlewaren i main.py)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_role, require_user
from portal.cache import kv
from portal.db import SessionLocal, get_session
from portal.models import MobPreset, Setting, User
from portal.services.auth import role_at_least
from portal.timeutil import dk_today

log = logging.getLogger("portal.mobgang")
router = APIRouter(prefix="/api/mobgang", tags=["mobgang"])

MAX_PRESETS = 100
OFFICIAL_ROLE = "editor"

# ---------------------------------------------------------------- ynglesæson

SEASON_KEY = "mobgang_season"
SEASON_DEFAULT = {"enabled": True, "from": "03-15", "to": "07-31"}  # MM-DD, begge dage inklusive


def _md(v: str) -> tuple[int, int]:
    m, d = (int(x) for x in v.split("-"))
    date(2024, m, d)  # skudår, så 29. februar er gyldig
    return m, d


def season_state(cfg: dict, today: date) -> dict:
    """Er modulet lukket i dag, og hvornår åbner/lukker det næste gang. Perioden må gå hen over nytår."""
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

    return {**cfg, "closed": closed,
            "opens": (next_day(t, today - timedelta(days=1)) + timedelta(days=1)).isoformat() if closed else None,
            "closes": next_day(f, today).isoformat() if cfg["enabled"] and not closed else None}


async def season(db: AsyncSession) -> dict:
    row = await db.get(Setting, SEASON_KEY)
    return season_state({**SEASON_DEFAULT, **(row.value if row and isinstance(row.value, dict) else {})}, dk_today())


class SeasonIn(BaseModel):
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


@router.put("/season")
async def put_season(body: SeasonIn, user: User = Depends(require_role("admin")), db: AsyncSession = Depends(get_session)):
    value = {"enabled": body.enabled, "from": body.from_, "to": body.to}
    row = await db.get(Setting, SEASON_KEY)
    if row is None:
        db.add(Setting(key=SEASON_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return await season(db)


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


async def audio_allowed(user_id: int) -> bool:
    """Må brugeren hente mobgang-lydfiler: uden for ynglesæsonen og med gyldig etiktest."""
    if not await kv().exists(f"mobgang:ethics:{user_id}"):
        return False
    async with SessionLocal() as db:
        return not (await season(db))["closed"]


@router.get("/status")
async def status(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    ethics = _ethics(user)
    await _remember(user, ethics)
    return {"season": await season(db), "ethics": ethics, "questions": [
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
    editable = role_at_least(user, OFFICIAL_ROLE) if p.official else bool(user and p.user_id == user.id)
    return {"id": p.id, "name": p.name, "config": p.config or {}, "official": p.official, "editable": editable,
            "updated_at": p.updated_at}


def _check(config: dict) -> None:
    if len(str(config)) > 8000:
        raise HTTPException(400, "Opsætningen er for stor")


def _may_official(user: User) -> None:
    if not role_at_least(user, OFFICIAL_ROLE):
        raise HTTPException(403, "Kun redaktører kan gemme officielle presets")


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

ALL = ("solsort", "sangdrossel", "vindrossel", "stendrossel", "nattergal", "blaahals", "blaastjert", "cettisanger", "gulbug",
       "hoegesanger", "lundsanger", "hvidbrynet", "schwarz", "brunloevsanger", "graafluesnapper", "lillefluesnapper",
       "hvidhalset", "brogetfluesnapper", "roedtoppet", "fyrremejse", "noeddekrige")


def _cfg(target: str = "", boost: int = 6, duck: int = -6, **species: int) -> dict:
    """species: art-id → antal individer (arter der ikke nævnes er slået fra)."""
    return {"v": 1, "master": 80, "target": target, "boost": boost, "duck": duck,
            "species": {s: {"on": s in species, "count": species.get(s, 1), "vol": 80} for s in ALL}}


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
}


async def seed_presets(session: AsyncSession) -> None:
    """Opret de officielle startpresets (redaktører kan derefter rette og slette dem)."""
    for key, presets_ in SEED.items():
        if await session.get(Setting, key) is not None:
            continue
        for name, cfg in presets_:
            session.add(MobPreset(user_id=None, name=name, config=cfg, official=True))
        session.add(Setting(key=key, value={"n": len(presets_)}))
        await session.commit()
        log.info("Oprettede %s officielle mobgang-presets (%s)", len(presets_), key)
