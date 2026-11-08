"""Læring: egne trænings­sessioner (foto og lyd), gemte træninger, repetition og «Forbered en tur» (docs/laering-spec.md §6–7)."""

from __future__ import annotations

import json
import random
import secrets
from datetime import date, datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.db import ObsSession
from portal.models import Course, LrTraining, Question, User
from portal.services import learn_gen, learn_play, sound_quiz, species_quiz
from portal.services import dataset

KILDER = ("gruppe", "arter", "mine", "afdeling", "lokalitet", "kursus", "svage")
MAX_ARTER = 600


class TrainError(Exception):
    pass


async def _valid(db: AsyncSession, artnrs: list[int]) -> list[int]:
    if not artnrs:
        return []
    rows = (await db.execute(text("SELECT artnr FROM species WHERE artnr = ANY(:a) AND art_type = 'art' AND list_artnr = artnr AND NOT excluded"), {"a": artnrs[:MAX_ARTER * 2]})).scalars().all()
    return sorted(set(rows))[:MAX_ARTER]


async def species_for(db: AsyncSession, user: User, cfg: dict) -> list[int]:
    """Artsudvalget for en træning ud fra ``kilde`` (se spec §6.2)."""
    kilde = cfg.get("kilde") or ("gruppe" if cfg.get("gruppe") else "arter")
    if kilde not in KILDER:
        raise TrainError("Ukendt kilde")
    if kilde in ("gruppe", "arter"):
        return await _valid(db, await learn_play.species_set(db, cfg))
    if kilde == "mine":
        if not user.obserkode:
            raise TrainError("Knyt din DOFbasen-profil for at bruge dine egne arter")
        async with ObsSession() as o:
            rows = (await dataset.execute(o, """SELECT o.artnr FROM observations o JOIN species s ON s.artnr = o.artnr AND s.art_type = 'art' AND s.list_artnr = s.artnr
                WHERE o.obserkode = :k GROUP BY o.artnr ORDER BY count(*) DESC LIMIT :n""", {"k": user.obserkode, "n": MAX_ARTER}, secret_for=user.obserkode)).scalars().all()
        return list(rows)
    if kilde == "afdeling":
        afd = int(cfg.get("afdeling_id") or 0)
        if not afd:
            raise TrainError("Vælg en afdeling")
        async with ObsSession() as o:
            rows = (await o.execute(text("""SELECT c.artnr FROM area_species_counts c JOIN species s ON s.artnr = c.artnr AND s.art_type = 'art' AND s.list_artnr = s.artnr
                WHERE c.area_key = :k ORDER BY c.n_obs DESC LIMIT 80"""), {"k": f"afd:{afd}"})).scalars().all()
        return list(rows)
    if kilde == "lokalitet":
        loknr = int(cfg.get("loknr") or 0)
        if not loknr:
            raise TrainError("Vælg en lokalitet")
        async with ObsSession() as o:
            rows = (await o.execute(text("""SELECT f.artnr FROM loc_species_finds f JOIN species s ON s.artnr = f.artnr AND s.art_type = 'art' AND s.list_artnr = s.artnr
                WHERE f.loknr = :l ORDER BY f.n_finds DESC LIMIT 80"""), {"l": loknr})).scalars().all()
        return list(rows)
    if kilde == "kursus":
        c = (await db.execute(select(Course).where(Course.slug == cfg.get("kursus", "")))).scalar_one_or_none()
        if c is None or not await learn_play.can_view(db, user, c):
            raise TrainError("Kurset findes ikke")
        st = await learn_play.struct(db, c)
        found: set[int] = set()
        for u in st["units"]:
            for b in u.get("blocks", []):
                if b.get("artnr"):
                    found.add(int(b["artnr"]))
            for a in u["acts"]:
                found |= set(await learn_play.species_set(db, a.get("config") or {}))
                found |= {int(x) for x in (a.get("config") or {}).get("par", [])}
        return await _valid(db, sorted(found))
    # svage: arter brugeren tit tager fejl af
    rows = (await db.execute(text("SELECT DISTINCT artnr FROM lr_species_mastery WHERE user_id = :u AND box <= 1 AND wrong > 0 LIMIT :n"), {"u": user.id, "n": MAX_ARTER})).scalars().all()
    return await _valid(db, list(rows))


async def build(db: AsyncSession, user: User, cfg: dict, foto_ok: bool, lyd_ok: bool) -> list[dict]:
    """En træning: ``m`` = foto / lyd / begge. Spørgsmålene har samme format som i kurser (id med art:/snd:)."""
    m = cfg.get("m", "foto")
    metode = cfg.get("metode", "valg")
    if m not in ("foto", "lyd", "begge") or metode not in ("valg", "tekst"):
        raise TrainError("Ukendt tilstand")
    n = max(1, min(40, int(cfg.get("n") or 20)))
    if (m in ("foto", "begge") and not foto_ok) or (m in ("lyd", "begge") and not lyd_ok):
        raise TrainError("Den valgte træningsform er ikke slået til")
    arter = await species_for(db, user, cfg)
    if len(arter) < species_quiz.MIN_SPECIES:
        raise TrainError("Der er ikke arter nok i udvalget – vælg flere")
    out: list[dict] = []
    try:
        if m in ("foto", "begge"):
            for q in await species_quiz.build(db, arter, metode, n if m == "foto" else (n + 1) // 2):
                out.append({"id": "art:" + q["id"], "type": "foto_art", "prompt": "Hvilken art er det?", "ask_confidence": True, "skills": ["Genkald"],
                            "media": {"kind": "image", "value": q["media_url"], "credit": q.get("media_credit") or ""}, "metode": metode, "choices": q.get("choices")})
        if m in ("lyd", "begge"):
            for q in await sound_quiz.build(db, arter, metode, cfg.get("rtype", "begge"), n if m == "lyd" else n // 2):
                out.append({"id": "snd:" + q["id"], "type": "lyd_art", "prompt": "Hvilken art synger her?", "ask_confidence": True, "skills": ["Lyt", "Genkald"],
                            "media": {"kind": "audio", "value": q["media_url"], "credit": ""}, "metode": metode, "choices": q.get("choices")})
    except (species_quiz.QuizError, sound_quiz.SoundError) as exc:
        if not out:
            raise TrainError(str(exc)) from exc
    random.shuffle(out)
    return out[:n]


# ---------------------------------------------------------------- gemte træninger

def clean_config(cfg: dict) -> dict:
    keep = {"kilde", "gruppe", "arter", "loknr", "afdeling_id", "kursus", "m", "metode", "rtype", "n"}
    out = {k: v for k, v in cfg.items() if k in keep}
    if isinstance(out.get("arter"), list):
        out["arter"] = [int(x) for x in out["arter"]][:MAX_ARTER]
    return out


async def save_training(db: AsyncSession, user: User, title: str, cfg: dict) -> LrTraining:
    t = LrTraining(user_id=user.id, title=title.strip()[:160] or "Min træning", config=clean_config(cfg))
    db.add(t)
    await db.commit()
    return t


async def share(db: AsyncSession, t: LrTraining) -> str:
    if not t.share_token:
        t.share_token = secrets.token_urlsafe(9)[:12]
        await db.commit()
    return t.share_token


# ---------------------------------------------------------------- repetition

async def due_counts(db: AsyncSession, user: User) -> dict:
    sp = (await db.execute(text("SELECT modality, count(*) FROM lr_species_mastery WHERE user_id = :u AND due_at <= now() GROUP BY modality"), {"u": user.id})).all()
    qn = (await db.execute(text("SELECT count(*) FROM question_progress WHERE user_id = :u AND due_at <= now()"), {"u": user.id})).scalar() or 0
    return {"foto": dict(sp).get("foto", 0), "lyd": dict(sp).get("lyd", 0), "spoergsmaal": qn}


async def repetition(db: AsyncSession, user: User, foto_ok: bool, lyd_ok: bool, limit: int = 20) -> list[dict]:
    """Det, der er forfaldent i dag: statiske spørgsmål og arter (foto/lyd), højst ``limit`` i alt."""
    out: list[dict] = []
    rng = random.Random()
    rows = (await db.execute(text("""SELECT p.question_id FROM question_progress p JOIN questions q ON q.id = p.question_id JOIN courses c ON c.id = q.course_id AND c.published
        WHERE p.user_id = :u AND p.due_at <= now() ORDER BY p.due_at LIMIT :n"""), {"u": user.id, "n": limit // 2})).scalars().all()
    for qid in rows:
        q = await db.get(Question, qid)
        c = await db.get(Course, q.course_id)
        if q.type not in learn_play.STATIC or not await learn_play.can_view(db, user, c):
            continue
        a = learn_play.find_act(c.published_snapshot, qid) if c.published_snapshot else learn_play.act_from_row(q)
        if a:
            out.append(await learn_play.public_static(db, a, rng, c.hue, list(c.skills or [])))
    for mod, ok, mk in (("foto", foto_ok, species_quiz.build), ("lyd", lyd_ok, None)):
        if not ok:
            continue
        due = (await db.execute(text("SELECT artnr FROM lr_species_mastery WHERE user_id = :u AND modality = :m AND due_at <= now() ORDER BY due_at LIMIT 10"), {"u": user.id, "m": mod})).scalars().all()
        if len(due) < 2:  # byggerne kræver mindst to arter – fyld op med nogle af brugerens øvrige
            more = (await db.execute(text("SELECT artnr FROM lr_species_mastery WHERE user_id = :u AND modality = :m AND artnr <> ALL(:x) ORDER BY box, due_at LIMIT 4"),
                                     {"u": user.id, "m": mod, "x": list(due) or [0]})).scalars().all()
            if not due:
                continue
            due = list(due) + list(more)
        try:
            if mod == "foto":
                for q in await species_quiz.build(db, list(due), "valg", len(due)):
                    out.append({"id": "art:" + q["id"], "type": "foto_art", "prompt": "Hvilken art er det?", "ask_confidence": True, "skills": ["Genkald"],
                                "media": {"kind": "image", "value": q["media_url"], "credit": q.get("media_credit") or ""}, "choices": q.get("choices")})
            else:
                for q in await sound_quiz.build(db, list(due), "valg", "begge", len(due)):
                    out.append({"id": "snd:" + q["id"], "type": "lyd_art", "prompt": "Hvilken art synger her?", "ask_confidence": True, "skills": ["Lyt", "Genkald"],
                                "media": {"kind": "audio", "value": q["media_url"], "credit": ""}, "choices": q.get("choices")})
        except (species_quiz.QuizError, sound_quiz.SoundError):
            continue
    rng.shuffle(out)
    return out[:limit]


# ---------------------------------------------------------------- forbered en tur

CLASSES = [("sikker", "Næsten sikker", 0.5, 2), ("sandsynlig", "Sandsynlig", 0.1, 0.5), ("mulig", "Mulig", 0.02, 0.1)]


async def photos_for(db: AsyncSession, artnrs: list[int]) -> dict[int, str]:
    if not artnrs:
        return {}
    rows = (await db.execute(text(f"""SELECT DISTINCT ON (b.artnr) b.artnr, m.thumb_key, m.medium_key, m.storage_key
        {species_quiz._SINGLE} AND b.artnr = ANY(:a) ORDER BY b.artnr, p.likes DESC"""), {"a": artnrs})).mappings().all()
    return {r["artnr"]: "/media/art/" + (r["thumb_key"] or r["medium_key"] or r["storage_key"]) for r in rows}


async def prepare(db: AsyncSession, loknr: int | None, dato: date) -> dict:
    """Forventede arter på en lokalitet (eller landsdækkende) i ugen omkring datoen, i klasser efter fundfrekvens."""
    week = learn_gen.week_index(dato)
    key = f"lr:forb:{loknr or 0}:{week}"
    cached = await kv().get(key)
    if cached:
        return json.loads(cached)
    sted, trips, note, items = None, 0, "", []
    if loknr:
        sted = await learn_gen.location(loknr)
        if sted is None:
            raise TrainError("Lokaliteten findes ikke")
        trips, freq = await learn_gen.loc_frequency(loknr, dato.timetuple().tm_yday, 10)
        if trips >= learn_gen.MIN_TRIPS_LOC:
            items = [{"artnr": a, "navn": v[0], "andel": v[1] / trips} for a, v in freq.items()]
        else:
            note = f"Der er kun {trips} ture på lokaliteten i perioden (mindst {learn_gen.MIN_TRIPS_LOC} kræves) – listen viser derfor hele landet."
    if not items:
        wk = await learn_gen.weekly()
        items = [{"artnr": int(a), "navn": w["navn"], "andel": w["rr"][week]} for a, w in wk.items() if w["rr"][week] >= 0.02]
        if not note:
            note = "Listen viser hele landet (vælg en lokalitet for at få den lokale)."
    items.sort(key=lambda x: -x["andel"])
    sp = {r["artnr"]: r for r in (await db.execute(text("SELECT artnr, latin, klass FROM species WHERE artnr = ANY(:a)"), {"a": [i["artnr"] for i in items[:300]]})).mappings()}
    photos = await photos_for(db, [i["artnr"] for i in items[:150]])
    groups = []
    for k, label, lo, hi in CLASSES:
        rows = [i for i in items if lo <= i["andel"] < hi][:40]
        groups.append({"key": k, "navn": label, "arter": [{**i, "andel": round(i["andel"], 3), "latin": sp.get(i["artnr"], {}).get("latin"), "foto": photos.get(i["artnr"])} for i in rows]})
    rare = [i for i in items if sp.get(i["artnr"], {}).get("klass") in ("SU", "SUB") and i["andel"] >= 0.005][:20]
    groups.append({"key": "sjaelden", "navn": "Sjælden her (SU/SUB)", "arter": [{**i, "andel": round(i["andel"], 4), "latin": sp[i["artnr"]]["latin"], "foto": photos.get(i["artnr"])} for i in rare]})
    res = {"sted": sted, "dato": dato.isoformat(), "uge": week + 1, "ture": trips, "note": note, "grupper": groups,
           "alle": [i["artnr"] for g in groups[:3] for i in g["arter"]]}
    await kv().set(key, json.dumps(res, default=str, ensure_ascii=False), ex=24 * 3600)
    return res
