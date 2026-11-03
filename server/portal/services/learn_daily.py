"""Læring: «Fugl for fugl» (dagens art) og færdighedstest (docs/laering-spec.md §12.2).

Dagens art er den samme for alle: den vælges ud fra datoen blandt arter, der er almindelige nu (fænologien) og har et foto. Ledetrådene
afsløres én ad gangen af serveren (aldrig alle på en gang), og jo færre der er brugt, jo flere point."""

from __future__ import annotations

import json
import random
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.services import learn_gen, learn_play, species_quiz

CLUES = ["fænologi", "slægt", "lyd", "foto"]
POINTS = [100, 70, 40, 20]


async def candidates(db: AsyncSession, day: date) -> list[int]:
    key = f"lr:dagens:kand:{day.isoformat()}"
    raw = await kv().get(key)
    if raw:
        return json.loads(raw)
    wk = await learn_gen.weekly()
    week = learn_gen.week_index(day)
    now = [int(a) for a, w in wk.items() if w["rr"][week] >= 0.05]
    if not now:
        return []
    have = (await db.execute(text(f"SELECT DISTINCT b.artnr {species_quiz._SINGLE} AND b.artnr = ANY(:a)"), {"a": now})).scalars().all()
    out = sorted(have)
    await kv().set(key, json.dumps(out), ex=86400)
    return out


async def todays(db: AsyncSession, day: date | None = None) -> dict | None:
    day = day or date.today()
    c = await candidates(db, day)
    if not c:
        return None
    artnr = random.Random(day.toordinal() * 7919).choice(c)
    sp = (await db.execute(text("SELECT artnr, navn, latin, klass, list_artnr FROM species WHERE artnr = :a"), {"a": artnr})).mappings().first()
    return dict(sp) if sp else None


async def clue(db: AsyncSession, sp: dict, step: int, day: date) -> dict:
    wk = await learn_gen.weekly()
    if step == 0:
        w = (wk.get(str(sp["artnr"])) or {}).get("rr")
        return {"trin": 0, "type": "faenologi", "titel": "Hvornår ses den?", "uger": w, "nu": learn_gen.week_index(day), "klass": sp["klass"]}
    if step == 1:
        genus = (sp["latin"] or "").split(" ")[0]
        return {"trin": 1, "type": "tekst", "titel": "Slægt", "tekst": f"Slægt: {genus}" if genus else "Slægten kendes ikke", "bogstaver": len(sp["navn"])}
    if step == 2:
        r = (await db.execute(text("SELECT id FROM snd_recordings WHERE artnr = :a AND enabled ORDER BY id LIMIT 1"), {"a": sp["artnr"]})).scalar()
        m = await learn_play.sound_media(db, r, hide=True) if r else None
        return {"trin": 2, "type": "lyd", "titel": "Lyt", "media": m, "ingen": m is None}
    p = (await db.execute(text(f"SELECT p.id {species_quiz._SINGLE} AND b.artnr = :a ORDER BY p.id LIMIT 1"), {"a": sp["artnr"]})).scalar()
    m = await learn_play.photo_media(db, p) if p else None
    return {"trin": 3, "type": "foto", "titel": "Foto", "media": m}


async def guess(db: AsyncSession, sp: dict, artnr: int, step: int) -> dict:
    ok = artnr == sp["artnr"]
    if not ok and artnr > 0:
        listed = (await db.execute(text("SELECT list_artnr FROM species WHERE artnr = :a"), {"a": artnr})).scalar()
        ok = listed is not None and listed == (sp["list_artnr"] or sp["artnr"])
    return {"correct": ok, "point": POINTS[min(step, 3)] if ok else 0, "art": {"artnr": sp["artnr"], "navn": sp["navn"], "latin": sp["latin"]} if ok or step >= 3 else None}
