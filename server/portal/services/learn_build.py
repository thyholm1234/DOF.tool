"""Læring: kursusbyggeren – redigering, advarsler, publicering (låst kopi), kopiering, import og eksport (docs/laering-spec.md §8)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import Course, Question, Unit, User
from portal.services import learn_gen, learn_play
from portal.services.auth import can

HUES = {"skov", "himmel", "sand", "rav", "sø", "hav", "eng", "skumring"}
SILS = {"spurvefugl", "mejse", "and", "svane", "maage", "rovfugl", "rovfugl_flugt", "hejre", "spaette", "svale", "vadefugl", "skarv", "due", "gaas"}
KINDS = {"kursus", "ovelse", "quiz", "spil"}
BLOCKS = {"tekst", "foto", "fotoraekke", "lyd", "silhuet", "artskort", "kort", "artikel", "tip", "advarsel", "metode"}


class BuildError(Exception):
    def __init__(self, status: int, msg: str, extra: dict | None = None):
        super().__init__(msg)
        self.status, self.msg, self.extra = status, msg, extra or {}


def slugify(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:70] or uuid.uuid4().hex[:8]


async def unique_slug(db: AsyncSession, base: str) -> str:
    slug, n = slugify(base), 1
    while (await db.execute(select(Course.id).where(Course.slug == slug))).first():
        n += 1
        slug = f"{slugify(base)[:64]}-{n}"
    return slug


# ---------------------------------------------------------------- rettigheder

async def load_editable(db: AsyncSession, user: User, slug: str) -> Course:
    c = (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
    if c is None:
        raise BuildError(404, "Kurset findes ikke")
    if not learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id)):
        raise BuildError(403, "Du må ikke redigere dette kursus")
    return c


def touch(c: Course, user: User) -> None:
    c.updated_at = datetime.now(timezone.utc)
    c.updated_by = user.id


def check_stamp(c: Course, stamp: str | None) -> None:
    """Optimistisk lås: klienten sender ``updated_at`` fra sidste hentning; er kurset ændret siden, svares 409."""
    if stamp and c.updated_at and c.updated_at.isoformat() != stamp:
        raise BuildError(409, "Kurset er ændret af en anden (eller i et andet vindue) – genindlæs.")


# ---------------------------------------------------------------- hele kurset til editoren

def act_full(q: Question) -> dict:
    return {**learn_play.act_from_row(q), "id": q.id}


async def full(db: AsyncSession, c: Course) -> dict:
    st = await learn_play.live_struct(db, c)
    editors = (await db.execute(text("SELECT u.id, u.navn FROM lr_course_editors e JOIN users u ON u.id = e.user_id WHERE e.course_id = :c"), {"c": c.id})).mappings().all()
    out = {**st, "course": {**st["course"], "updated_at": c.updated_at.isoformat() if c.updated_at else None, "created_by": c.created_by, "has_snapshot": bool(c.published_snapshot)},
           "changed": changed_since_publish(c, st), "editors": [dict(e) for e in editors]}
    out["warnings"] = await warnings(db, st)
    return out


_META = {"id", "published", "visibility", "version", "has_snapshot", "updated_at", "created_by", "number_unused"}


def _content(st: dict) -> dict:
    """Kursets indhold uden id'er og metadata – til sammenligning mellem levende rækker og den låste kopi."""
    def act(a):
        return {k: v for k, v in a.items() if k not in ("id", "unit_id", "position")}

    return {"course": {k: v for k, v in st["course"].items() if k not in _META},
            "units": [{**{k: v for k, v in u.items() if k not in ("id", "acts")}, "acts": [act(a) for a in u["acts"]]} for u in st["units"]]}


def changed_since_publish(c: Course, live: dict) -> bool:
    if not c.published_snapshot:
        return True
    a = json.loads(json.dumps(_content(live), default=str, sort_keys=True))
    b = json.loads(json.dumps(_content(c.published_snapshot), default=str, sort_keys=True))
    return a != b


# ---------------------------------------------------------------- advarsler (spec §8.2)

def act_problems(a: dict) -> list[tuple[str, str]]:
    """[(niveau, tekst)] for én aktivitet. ``fejl`` blokerer publicering, ``advarsel`` gør ikke."""
    t, cfg, out = a["type"], a.get("config") or {}, []
    if t in learn_play.STATIC and t not in ("refleksion",) and not (a.get("prompt") or "").strip():
        out.append(("fejl", "Spørgsmålet er tomt"))
    if t in ("valg", "silhuet"):
        ch = [c for c in a["choices"] if (c.get("text") or "").strip()]
        if len(ch) < 2:
            out.append(("fejl", "Mindst to svarmuligheder"))
        right = sum(1 for c in ch if c.get("correct"))
        if right == 0:
            out.append(("fejl", "Intet svar er markeret som rigtigt"))
        elif right == len(ch):
            out.append(("fejl", "Alle svar er rigtige"))
    elif t == "tekst" and not [x for x in a.get("accept") or [] if x.strip()]:
        out.append(("fejl", "Angiv mindst ét godkendt svar"))
    elif t == "raekkefoelge" and len([x for x in cfg.get("items", []) if str(x).strip()]) < 2:
        out.append(("fejl", "Angiv mindst to elementer i rækkefølgen"))
    elif t == "billedpunkt":
        if not (a.get("media_photo_id") or a.get("media_url")):
            out.append(("fejl", "Vælg et billede"))
        if not any(z.get("correct") for z in cfg.get("zones", [])):
            out.append(("fejl", "Markér mindst ét rigtigt område i billedet"))
    elif t in ("foto_art", "lyd_art") and not (cfg.get("arter") or cfg.get("gruppe")):
        out.append(("fejl", "Vælg en artsgruppe eller nogle arter"))
    elif t in ("foto_art", "lyd_art") and not cfg.get("gruppe") and len(cfg.get("arter") or []) < 2:
        out.append(("fejl", "Vælg mindst to arter"))
    elif t == "foto_sammenlign" and not (len(cfg.get("par") or []) == 2 and cfg["par"][0] != cfg["par"][1]):
        out.append(("fejl", "Vælg to forskellige arter"))
    elif t == "refleksion" and not (a.get("prompt") or "").strip():
        out.append(("fejl", "Skriv en opgave"))
    elif t not in learn_play.TYPES:
        out.append(("fejl", f"Ukendt aktivitetstype {t}"))
    if t in ("valg", "silhuet", "tekst", "raekkefoelge", "billedpunkt") and not (a.get("explanation") or "").strip():
        out.append(("advarsel", "Mangler forklaring"))
    if a.get("media_url") and not (a.get("media_credit") or "").strip() and not a["media_url"].startswith("/media/art/"):
        out.append(("advarsel", "Medie uden kreditering"))
    return out


async def warnings(db: AsyncSession, st: dict) -> list[dict]:
    out: list[dict] = []
    c = st["course"]

    def add(level, where, msg, **kw):
        out.append({"level": level, "where": where, "msg": msg, **kw})

    if c["kind"] == "kursus":
        if not c["learn"]:
            add("advarsel", "kursus", "Kurset har ingen læringsmål")
        if not [u for u in st["units"] if u["id"]]:
            add("fejl", "kursus", "Kurset har ingen lektioner")
    if not (c["title"] or "").strip():
        add("fejl", "kursus", "Titel mangler")
    photo_ids = []
    for u in st["units"]:
        if u["id"] and not u["acts"] and not (u["lesson_md"] or "").strip() and not u["blocks"]:
            add("advarsel", f"lektion {u['id']}", f"Lektionen «{u['title']}» er tom", unit=u["id"])
        elif u["id"] and not u["acts"]:
            add("advarsel", f"lektion {u['id']}", f"Lektionen «{u['title']}» har ingen aktiviteter", unit=u["id"])
        for b in u["blocks"]:
            if b.get("type") == "foto" and b.get("photo_id"):
                photo_ids.append(int(b["photo_id"]))
            # automatiske fotos vælges kun blandt dem, DOF må bruge – advar, hvis der ikke er noget
            refs = []
            if b.get("type") == "foto" and b.get("ref") and not b.get("photo_id"):
                refs = [b["ref"]]
            elif b.get("type") == "fotoraekke" and not b.get("photo_ids"):
                refs = b.get("refs") or []
            missing = [r for r in refs if not await learn_play.photo_by_ref(db, r)]
            if missing:
                names = ", ".join(sorted({await _ref_name(db, r) for r in missing}))
                add("advarsel", f"lektion {u['id']}", f"Lektionen «{u['title']}»: intet foto, DOF må bruge, af {names} – vælg et selv eller fjern det", unit=u["id"])
            if b.get("type") == "fotoraekke":
                photo_ids += [int(x) for x in b.get("photo_ids") or []]
        for a in u["acts"]:
            if a.get("media_photo_id"):
                photo_ids.append(int(a["media_photo_id"]))
            for lvl, msg in act_problems(a):
                add(lvl, f"aktivitet {a['id']}", msg, act=a["id"], unit=u["id"])
            # generatorer, der giver for få forskellige spørgsmål
            cfg = a.get("config") or {}
            if a["type"] in ("foto_art", "lyd_art") and cfg.get("arter") and not cfg.get("gruppe") and len(cfg["arter"]) < 5 and int(cfg.get("antal") or 1) > len(cfg["arter"]):
                add("advarsel", f"aktivitet {a['id']}", "Få arter i forhold til antal spørgsmål – spørgsmålene vil gentage sig", act=a["id"], unit=u["id"])
    if photo_ids:
        rows = (await db.execute(text("""SELECT p.id, coalesce(p.rettighed, ph.standard_rettighed) AS ret, coalesce(ph.dof_samtykke, false) AS samtykke
            FROM gal_photos p LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id WHERE p.id = ANY(:i)"""), {"i": sorted(set(photo_ids))})).mappings().all()
        found = {r["id"] for r in rows}
        for r in rows:
            if r["ret"] in (2, 3) and not r["samtykke"]:
                add("fejl", f"foto {r['id']}", f"Foto {r['id']} har rettighed {r['ret']} og må ikke indsættes fast (fotografen har ikke givet DOF samtykke)", photo=r["id"])
        for i in sorted(set(photo_ids) - found):
            add("fejl", f"foto {i}", f"Foto {i} findes ikke eller er ikke publiceret", photo=i)
    return out


# ---------------------------------------------------------------- redigering

COURSE_FIELDS = ("title", "summary", "intro", "topic", "level", "kind", "number", "stage", "category", "card", "audience", "learn", "skills", "hours",
                 "hue", "sil", "visibility", "cover_media_id", "featured")


def clean_course_fields(user: User, c: Course, data: dict) -> dict:
    out: dict = {}
    for k in COURSE_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "kind" and v not in KINDS:
            raise BuildError(400, "Ukendt type")
        if k == "hue" and v not in HUES:
            raise BuildError(400, "Ukendt farveflade")
        if k == "sil" and v not in SILS:
            raise BuildError(400, "Ukendt silhuet")
        if k == "visibility" and v not in ("alle", "login", "hold"):
            raise BuildError(400, "Ukendt synlighed")
        if k == "skills":
            v = [s for s in v if s in learn_play.SKILLS][:9]
        if k == "learn":
            v = [{"title": str(x.get("title", ""))[:120], "text": str(x.get("text", ""))[:400]} for x in v if isinstance(x, dict)][:12]
        if k == "featured" and not can(user, "laering"):
            continue
        if k in ("title", "stage", "category", "hours") and isinstance(v, str):
            v = v.strip()[: {"title": 160, "stage": 40, "category": 40, "hours": 20}[k]]
        if k in ("summary", "card", "audience") and isinstance(v, str):
            v = v[:1000]
        if k == "intro" and isinstance(v, str):
            v = v[:20000]
        out[k] = v
    return out


async def create_course(db: AsyncSession, user: User, title: str, kind: str = "kursus") -> Course:
    if kind not in KINDS:
        raise BuildError(400, "Ukendt type")
    c = Course(slug=await unique_slug(db, title), title=title.strip()[:160] or "Nyt kursus", kind=kind, published=False, created_by=user.id,
               visibility="hold" if not can(user, "laering") else "alle", updated_at=datetime.now(timezone.utc), updated_by=user.id)
    db.add(c)
    await db.flush()
    if kind == "kursus":
        db.add(Unit(course_id=c.id, position=0, title="Lektion 1", lesson_md="", blocks=[]))
    await db.commit()
    return c


async def _ref_name(db: AsyncSession, r: dict) -> str:
    if r.get("artnr"):
        return (await db.execute(text("SELECT navn FROM species WHERE artnr = :a"), {"a": r["artnr"]})).scalar() or f"art {r['artnr']}"
    return str(r.get("latin") or f"netfugl-foto {r.get('nf')}")


def _clean_ref(r) -> dict | None:
    """En medie-reference: ``nf`` (netfugl-foto), ``xc`` (xeno-canto-nr.), ``artnr``, ``n`` (tal) og ``latin``, ``type`` (sang/kald)."""
    if not isinstance(r, dict):
        return None
    out: dict = {}
    for k in ("nf", "xc", "artnr", "n"):
        if str(r.get(k, "")).isdigit() and len(str(r[k])) <= 12:
            out[k] = str(r[k]) if k == "xc" else int(r[k])
    if isinstance(r.get("latin"), str) and r["latin"].strip():
        out["latin"] = r["latin"].strip()[:80]
    if r.get("type") in ("sang", "kald"):
        out["type"] = r["type"]
    return out or None


def clean_blocks(blocks: list) -> list[dict]:
    out = []
    for b in blocks or []:
        if not isinstance(b, dict) or b.get("type") not in BLOCKS:
            continue
        ref = _clean_ref(b.get("ref"))
        b = {k: v for k, v in b.items() if isinstance(v, (str, int, float, bool, list, type(None)))}
        if ref:  # automatisk foto/lyd (learn_play.photo_by_ref/rec_by_ref)
            b["ref"] = ref
        if isinstance(b.get("md"), str):
            b["md"] = b["md"][:20000]
        if isinstance(b.get("photo_ids"), list):
            b["photo_ids"] = [int(x) for x in b["photo_ids"] if str(x).isdigit()][:8]
        if isinstance(b.get("refs"), list):
            b["refs"] = [r for r in (_clean_ref(x) for x in b["refs"]) if r][:8]
        if isinstance(b.get("labels"), list):
            b["labels"] = [str(x)[:120] for x in b["labels"]][:8]
        out.append(b)
    return out[:60]


def clean_act(data: dict) -> dict:
    t = data.get("type", "valg")
    if t not in learn_play.TYPES:
        raise BuildError(400, "Ukendt aktivitetstype")
    cfg = data.get("config") or {}
    if len(json.dumps(cfg)) > 20000:
        raise BuildError(400, "Opsætningen er for stor")
    ch = [{"text": str(c.get("text", ""))[:300], "correct": bool(c.get("correct"))} for c in data.get("choices") or [] if isinstance(c, dict)][:8]
    mu = data.get("media_url") or None
    if mu and not (mu.startswith("/media/") or mu.startswith("https://")):
        raise BuildError(400, "Medie-URL skal være en upload eller https")
    mk = data.get("media_kind") or None
    if mk not in (None, "image", "audio"):
        raise BuildError(400, "Ukendt mediatype")
    return {"type": t, "prompt": str(data.get("prompt", ""))[:2000], "choices": ch, "explanation": str(data.get("explanation", ""))[:4000], "config": cfg,
            "accept": [str(x)[:200] for x in data.get("accept") or []][:20], "skills": [s for s in data.get("skills") or [] if s in learn_play.SKILLS][:6],
            "media_kind": mk if mu else None, "media_url": mu, "media_credit": (str(data.get("media_credit") or "")[:200] or None) if mu else None,
            "media_photo_id": data.get("media_photo_id") or None, "media_rec_id": data.get("media_rec_id") or None, "ask_confidence": data.get("ask_confidence") is not False}


async def reorder(db: AsyncSession, c: Course, units: list[int], acts: dict[str, list[int]]) -> None:
    """Gem rækkefølge og placering: ``units`` = lektionernes id i rækkefølge, ``acts`` = {lektions-id eller "0": [aktivitets-id …]}."""
    own_units = set((await db.execute(select(Unit.id).where(Unit.course_id == c.id))).scalars())
    own_acts = set((await db.execute(select(Question.id).where(Question.course_id == c.id))).scalars())
    for i, uid in enumerate(units):
        if uid in own_units:
            await db.execute(update(Unit).where(Unit.id == uid).values(position=i))
    for key, ids in acts.items():
        unit_id = int(key) or None
        if unit_id and unit_id not in own_units:
            continue
        for i, qid in enumerate(ids):
            if qid in own_acts:
                await db.execute(update(Question).where(Question.id == qid).values(unit_id=unit_id, position=i))


# ---------------------------------------------------------------- publicering

async def publish(db: AsyncSession, user: User, c: Course) -> Course:
    st = await learn_play.live_struct(db, c)
    errs = [w for w in await warnings(db, st) if w["level"] == "fejl"]
    if errs:
        raise BuildError(400, f"{len(errs)} fejl skal rettes, før kurset kan publiceres", {"warnings": errs})
    c.published_snapshot = json.loads(json.dumps(st, default=str))
    c.published_snapshot["course"]["version"] = (c.version or 0) + 1
    c.version = (c.version or 0) + 1
    c.published = True
    if not can(user, "laering"):
        c.visibility = "hold"  # undervisere publicerer kun til egne hold
    touch(c, user)
    await db.commit()
    return c


async def revert(db: AsyncSession, user: User, c: Course) -> None:
    """Fortryd ændringer: læg den låste kopi tilbage. Lektioner og aktiviteter, der findes i kopien, rettes på stedet (så fremgangen bevares);
    nye fjernes, og slettede genoprettes."""
    if not c.published_snapshot:
        raise BuildError(400, "Kurset er aldrig publiceret")
    snap = c.published_snapshot
    for k, v in snap["course"].items():
        if k in ("title", "summary", "intro", "topic", "level", "kind", "number", "stage", "category", "card", "audience", "learn", "skills", "hours", "hue", "sil", "cover_media_id"):
            setattr(c, k, v)
    keep_units = {u["id"] for u in snap["units"] if u.get("id") is not None}
    keep_acts = {a["id"] for u in snap["units"] for a in u["acts"]}
    now_units = set((await db.execute(select(Unit.id).where(Unit.course_id == c.id))).scalars())
    now_acts = set((await db.execute(select(Question.id).where(Question.course_id == c.id))).scalars())
    await db.execute(delete(Question).where(Question.id.in_(now_acts - keep_acts)))
    await db.execute(delete(Unit).where(Unit.id.in_(now_units - keep_units)))
    await db.flush()
    pos = 0
    for i, u in enumerate(snap["units"]):
        unit = None
        if u.get("id") is not None:
            unit = await db.get(Unit, u["id"]) if u["id"] in now_units else None
            if unit is None:
                unit = Unit(course_id=c.id)
                db.add(unit)
            unit.position, unit.title, unit.lesson_md, unit.blocks = i, u["title"], u["lesson_md"], u["blocks"]
            unit.min_score_pct, unit.est_minutes = u.get("min_score_pct"), u.get("est_minutes")
            await db.flush()
        for a in u["acts"]:
            pos += 1
            q = await db.get(Question, a["id"]) if a["id"] in now_acts else None
            if q is None:
                q = Question(course_id=c.id)
                db.add(q)
            q.unit_id, q.position = (unit.id if unit else None), pos
            for k in ("prompt", "choices", "explanation", "media_kind", "media_url", "media_credit", "type", "config", "accept", "skills", "media_photo_id", "media_rec_id", "ask_confidence"):
                setattr(q, k, a.get(k))
            q.config, q.accept, q.skills = a.get("config") or {}, a.get("accept") or [], a.get("skills") or []
    touch(c, user)
    await db.commit()


async def duplicate(db: AsyncSession, user: User, c: Course) -> Course:
    st = await learn_play.live_struct(db, c)
    n = Course(slug=await unique_slug(db, c.title + " kopi"), title=f"{c.title} (kopi)", created_by=user.id, published=False, kind=c.kind, visibility="hold" if not can(user, "laering") else c.visibility,
               **{k: getattr(c, k) for k in ("summary", "intro", "topic", "level", "number", "stage", "category", "card", "audience", "learn", "skills", "hours", "hue", "sil", "cover_media_id")})
    n.updated_at, n.updated_by = datetime.now(timezone.utc), user.id
    db.add(n)
    await db.flush()
    await restore_struct(db, n, st)
    await db.commit()
    return n


# ---------------------------------------------------------------- eksempler på generatorer (spec §8.2)

async def peek_answer(db: AsyncSession, qid: str, q: dict) -> str | None:
    from portal.cache import kv

    prefix, _, tok = str(qid).partition(":")
    key = {"lr": "quiz:lr:", "art": "quiz:art:", "snd": "quiz:snd:", "auto": "quiz:auto:"}.get(prefix)
    raw = await kv().get(key + tok) if key else None
    if not raw:
        return None
    d = json.loads(raw)
    if prefix in ("art", "snd"):
        return d.get("navn")
    if prefix == "auto":
        return next((c["text"] for c in q.get("choices", []) if c["id"] == d["correct"]), None)
    return d.get("correct")


async def examples(db: AsyncSession, user: User, a: dict, n: int = 5) -> list[dict]:
    import random

    a = {**a, "id": 0, "config": {**(a.get("config") or {}), "antal": min(n, 5)}}
    qs = await learn_play.expand_generator(db, user, a, random.Random(), [])
    out = []
    for q in qs:
        q = dict(q)
        if not q.get("locked"):
            q["facit"] = await peek_answer(db, q["id"], q)
        out.append(q)
    return out


# ---------------------------------------------------------------- import og eksport

def _from_traekrute(data: dict) -> list[dict]:
    """Trækrute-format (app/site/js/store.js): kurser med lektioner og øvelser."""
    out = []
    for c in data.get("courses", []):
        units = [{"title": u.get("title", ""), "lesson_md": u.get("lesson", ""), "blocks": [], "acts": [_tq(q) for q in u.get("questions", [])]} for u in c.get("units", [])]
        out.append({"kind": "kursus", **{k: c.get(k) for k in ("slug", "title", "summary", "intro", "topic", "level", "number", "stage", "card", "audience", "hours", "hue", "sil", "skills", "learn")},
                    "published": False, "units": units})
    for d in data.get("drills", []):
        out.append({"kind": d.get("kind", "ovelse"), "slug": d.get("slug"), "title": d.get("title"), "summary": d.get("summary", ""), "card": d.get("short", ""), "category": d.get("category", ""),
                    "skills": d.get("skills", []), "hue": d.get("hue"), "sil": d.get("sil"), "featured": d.get("featured", False), "published": False,
                    "units": [{"title": "", "lesson_md": "", "blocks": [], "acts": [_tq(q) for q in d.get("questions", [])]}], "loose": True})
    return out


def _tq(q: dict) -> dict:
    m = q.get("media") or {}
    a = {"type": "tekst" if q.get("type") == "tekst" else "valg", "prompt": q.get("prompt", ""), "explanation": q.get("explanation", ""), "choices": q.get("choices", []), "accept": q.get("accept", []), "config": {}}
    if m.get("kind") == "silhouette":
        a["type"], a["config"] = "silhuet", {"sil": m.get("value", "spurvefugl")}
    elif m.get("kind") in ("image", "audio") and m.get("value"):
        a.update(media_kind=m["kind"], media_url=m["value"], media_credit=m.get("credit", ""))
    return a


def _from_dof(data: dict) -> list[dict]:
    out = []
    for c in data.get("courses", []):
        acts = []
        for q in c.get("questions", []):
            acts.append({"type": "valg", "prompt": q["prompt"], "choices": q["choices"], "explanation": q.get("explanation", ""), "media_kind": q.get("media_kind"),
                         "media_url": q.get("media_url"), "media_credit": q.get("media_credit"), "config": {}})
        out.append({"kind": "kursus", "slug": c.get("slug"), "title": c["title"], "summary": c.get("summary", ""), "intro": c.get("intro", ""), "topic": c.get("topic", "id"),
                    "level": c.get("level", "øvet"), "published": False, "units": [{"title": "Spørgsmål", "lesson_md": c.get("intro", ""), "blocks": [], "acts": acts}]})
    return out


def normalize_import(data: dict) -> list[dict]:
    if data.get("format") == "laering-v2":
        return data.get("courses", [])
    if "drills" in data or any("units" in c for c in data.get("courses", [])):
        return _from_traekrute(data)
    return _from_dof(data)


def _small_int(v, hi: int) -> int | None:
    return max(0, min(int(v), hi)) if isinstance(v, int) or (isinstance(v, str) and v.isdigit()) else None


async def import_courses(db: AsyncSession, user: User, data: dict, mode: str = "kopi", dry: bool = False) -> list[dict]:
    """``mode``: ``kopi`` = ny kladde med unik slug · ``overskriv`` = erstat indholdet i en eksisterende kladde (kun kladder)."""
    report = []
    for c in normalize_import(data):
        slug = slugify(c.get("slug") or c.get("title") or "kursus")
        exists = (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
        status = "ny" if not exists else ("overskriv" if mode == "overskriv" and not exists.published else "kopi")
        n_acts = sum(len(u["acts"]) for u in c.get("units", []))
        report.append({"slug": slug, "title": c.get("title"), "status": status, "n_units": len(c.get("units", [])), "n_acts": n_acts})
        if dry:
            continue
        if status == "overskriv":
            if not learn_play.can_edit_course(user, exists, await learn_play.editor_ids(db, exists.id)):
                raise BuildError(403, f"Du må ikke overskrive {slug}")
            course = exists
            await db.execute(delete(Question).where(Question.course_id == course.id))
            await db.execute(delete(Unit).where(Unit.course_id == course.id))
            await db.flush()
        else:
            course = Course(slug=await unique_slug(db, slug if status == "ny" else slug + "-import"), title=(c.get("title") or "Uden titel")[:160], created_by=user.id)
            db.add(course)
        fields = {k: c[k] for k in ("summary", "intro", "topic", "level", "number", "stage", "category", "card", "audience", "learn", "skills", "hours", "hue", "sil", "featured") if c.get(k) is not None}
        fields = clean_course_fields(user, course, {**fields, "kind": c.get("kind", "kursus"), "title": c.get("title") or "Uden titel"})
        for k, v in fields.items():
            setattr(course, k, v)
        course.published = False
        course.visibility = "hold" if not can(user, "laering") else (course.visibility or "alle")
        touch(course, user)
        await db.flush()
        pos = 0
        for i, u in enumerate(c.get("units", [])):
            unit = None
            if not c.get("loose"):
                unit = Unit(course_id=course.id, position=i, title=(u.get("title") or f"Lektion {i + 1}")[:160], lesson_md=u.get("lesson_md", ""), blocks=clean_blocks(u.get("blocks")),
                            est_minutes=_small_int(u.get("est_minutes"), 600), min_score_pct=_small_int(u.get("min_score_pct"), 100))
                db.add(unit)
                await db.flush()
            for a in u.get("acts", []):
                pos += 1
                db.add(Question(course_id=course.id, unit_id=unit.id if unit else None, position=pos, **clean_act(a)))
        await db.flush()
    if not dry:
        await db.commit()
    return report


async def export_courses(db: AsyncSession, user: User, slugs: list[str] | None) -> dict:
    q = select(Course).order_by(Course.number.nulls_last(), Course.title)
    if slugs:
        q = q.where(Course.slug.in_(slugs))
    out = []
    for c in (await db.execute(q)).scalars():
        if not learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id)):
            continue
        st = await learn_play.live_struct(db, c)
        out.append({**{k: st["course"][k] for k in ("slug", "title", "summary", "intro", "topic", "level", "kind", "number", "stage", "category", "card", "audience", "learn", "skills", "hours", "hue", "sil", "featured")},
                    "units": [{"title": u["title"], "lesson_md": u["lesson_md"], "blocks": u["blocks"], "est_minutes": u.get("est_minutes"), "min_score_pct": u.get("min_score_pct"), "acts": [{k: a[k] for k in ("type", "prompt", "choices", "explanation", "config", "accept", "skills", "media_kind", "media_url", "media_credit", "media_photo_id", "media_rec_id", "ask_confidence")} for a in u["acts"]]} for u in st["units"]],
                    "loose": not any(u["id"] for u in st["units"])})
    return {"format": "laering-v2", "exported": datetime.now(timezone.utc).isoformat(), "courses": out}


# ---------------------------------------------------------------- medie-søgning til byggeren

async def search_photos(db: AsyncSession, q: str, artnr: int | None, limit: int = 40, offset: int = 0) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT p.id, m.thumb_key, m.medium_key, m.storage_key, ph.navn AS fotograf, coalesce(p.rettighed, ph.standard_rettighed) AS ret, coalesce(ph.dof_samtykke, false) AS samtykke,
               (SELECT string_agg(s.navn, ', ') FROM gal_birds b JOIN species s ON s.artnr = b.artnr WHERE b.photo_id = p.id) AS arter, p.lokalitet, p.dato, p.likes
        FROM gal_photos p JOIN content_media m ON m.id = p.media_id LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
        WHERE p.status = 'publiceret' AND (CAST(:a AS int) IS NULL OR EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.artnr = :a))
          AND (:q = '' OR EXISTS (SELECT 1 FROM gal_birds b JOIN species s ON s.artnr = b.artnr WHERE b.photo_id = p.id AND s.navn ILIKE :ql) OR ph.navn ILIKE :ql OR p.lokalitet ILIKE :ql)
        ORDER BY p.likes DESC, p.id DESC LIMIT :n OFFSET :o"""), {"a": artnr, "q": q.strip(), "ql": f"%{q.strip()}%", "n": limit, "o": offset})).mappings().all()
    return [{"id": r["id"], "thumb": "/media/art/" + (r["thumb_key"] or r["medium_key"] or r["storage_key"]), "fotograf": r["fotograf"], "rettighed": r["ret"], "samtykke": r["samtykke"],
             "fast_ok": r["ret"] == 1 or r["samtykke"], "arter": r["arter"], "lokalitet": r["lokalitet"], "dato": r["dato"].isoformat() if r["dato"] else None} for r in rows]


async def search_sounds(db: AsyncSession, artnr: int) -> list[dict]:
    rows = (await db.execute(text("""SELECT r.id, r.source, r.ext_id, r.recordist, r.license, r.rtype, r.quality, r.length_s, r.page_url, s.navn
        FROM snd_recordings r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.artnr = :a AND r.enabled ORDER BY r.quality NULLS LAST, r.id LIMIT 60"""), {"a": artnr})).mappings().all()
    return [dict(r) for r in rows]
