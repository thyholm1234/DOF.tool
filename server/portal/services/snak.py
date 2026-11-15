"""Snak: opslag, feed, trådside, følg, visninger og kategorier (docs/snak-modul-spec.md §4, §6)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.models import SnCategory, SnPost, User
from portal.services import content_render, moderation, snak_reactions
from portal.services.setting_store import get_setting_dict, put_setting

KEY = "snak"
DEFAULTS: dict = {"feed_kommentarer": 3, "feed_svar": 1, "ret_minutter": 1440, "opslag_pr_time": 6, "kommentarer_pr_minut": 5,
                  "max_billeder_opslag": 10, "max_pinned": 3}
MAX_BODY = 10_000
SORTS = ("aktivitet", "nyeste", "populaere")
PER_PAGE = 15

SEED_CATEGORIES = [
    ("artsbestemmelse", "Artsbestemmelse", {"har_loesning": True}, "Hjælp til at bestemme en fugl – vis billeder, lyd og omstændigheder.", "#2f7d5b"),
    ("artikler", "Artikler og læsning", {}, "Nye artikler, bøger, rapporter og videnskab – del og diskutér.", "#3b6ea5"),
    ("fund", "Fund og oplevelser", {}, "Dagens tur, gode fund og stemningsbilleder.", "#c27c1d"),
    ("diskussion", "Diskussion", {"kraever_titel": True}, "Fuglebeskyttelse, taksonomi, etik i felten, DOFbasen og meget mere.", "#8a4fa0"),
    ("ture", "Ture og arrangementer", {}, "Fælles ture, samkørsel og arrangementer.", "#b5473a"),
    ("udstyr", "Udstyr og foto", {}, "Kikkerter, kameraer, optagere, apps og teknik.", "#4f6b7a"),
    ("spoergsmaal", "Spørgsmål og hjælp", {"har_loesning": True}, "Alt det andet – også spørgsmål om DOF.tool.", "#6a7f2d"),
    ("meddelelser", "Meddelelser", {"kun_staff": True}, "Nyt fra redaktionen og moderatorerne.", "#a33a5b"),
]


class SnakError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _iso(v):
    return v.isoformat() if v else None


async def settings(session: AsyncSession) -> dict:
    return await get_setting_dict(session, KEY, DEFAULTS)


async def save_settings(session: AsyncSession, patch: dict) -> dict:
    cur = await settings(session)
    for k, v in patch.items():
        if k in DEFAULTS and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100_000:
            cur[k] = v
    await put_setting(session, KEY, cur)
    return cur


# ---------------------------------------------------------------- kategorier

async def seed_categories(session: AsyncSession) -> None:
    if (await session.execute(text("SELECT 1 FROM sn_categories LIMIT 1"))).first():
        return
    for i, (slug, navn, flags, beskrivelse, farve) in enumerate(SEED_CATEGORIES):
        session.add(SnCategory(slug=slug, navn=navn, beskrivelse=beskrivelse, farve=farve, sort=(i + 1) * 10, **flags))
    await session.commit()


async def categories(session: AsyncSession, user: User | None = None, *, include_archived: bool = False) -> list[dict]:
    """Aktive kategorier med antal opslag og antal opslag med aktivitet siden brugerens sidste besøg i modulet."""
    rows = (await session.execute(text(f"""
        SELECT c.id, c.slug, c.navn, c.beskrivelse, c.farve, c.sort, c.kraever_titel, c.har_loesning, c.kun_staff, c.arkiveret,
               (SELECT count(*) FROM sn_posts p WHERE p.category_id = c.id AND p.status = 'synlig') AS antal
        FROM sn_categories c {'' if include_archived else 'WHERE NOT c.arkiveret'} ORDER BY c.sort, c.id"""))).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- opslag: læsning

def _author(r) -> dict:
    if r["user_id"] is None:
        return {"navn": r["eksempel_navn"] or "Slettet bruger", "eksempel": True}
    return {"navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"], "user_id": r["user_id"]}


def post_out(r, viewer_id: int, staff: bool) -> dict:
    hidden = r["status"] == "skjult"
    mine = r["user_id"] == viewer_id and r["user_id"] is not None
    show = not hidden or staff or mine
    out = {"id": r["id"], "category": {"id": r["category_id"], "slug": r["cat_slug"], "navn": r["cat_navn"], "farve": r["cat_farve"]},
           "author": _author(r), "mine": mine, "titel": r["titel"], "html": r["body_html"] if show else "", "status": r["status"],
           "md": r["body_md"] if mine else None, "locked": r["locked"], "pinned": r["pinned_at"] is not None, "solved": r["solution_comment_id"] is not None,
           "har_loesning": r["cat_loesning"], "eksempel": r["eksempel"], "n_comments": r["n_comments"], "n_reactions": r["n_reactions"],
           "views": r["views"], "created_at": _iso(r["created_at"]), "edited_at": _iso(r["edited_at"]), "last_activity_at": _iso(r["last_activity_at"]),
           "arter": r["arter"] or [], "attachment": r["attachment"] if show else None, "following": bool(r["following"])}
    if hidden:
        out["hidden_reason"] = r["hidden_reason"] if staff or mine else None
    return out


_SELECT = """
    SELECT p.id, p.category_id, p.user_id, p.eksempel_navn, p.titel, p.body_md, p.body_html, p.attachment, p.arter, p.status, p.hidden_reason, p.locked,
           p.pinned_at, p.solution_comment_id, p.n_comments, p.n_reactions, p.views, p.last_activity_at, p.eksempel, p.created_at, p.edited_at,
           c.slug AS cat_slug, c.navn AS cat_navn, c.farve AS cat_farve, c.har_loesning AS cat_loesning, u.navn, u.obserkode,
           EXISTS (SELECT 1 FROM sn_follows f WHERE f.post_id = p.id AND f.user_id = :viewer AND NOT f.muted) AS following{extra}
    FROM sn_posts p JOIN sn_categories c ON c.id = p.category_id LEFT JOIN users u ON u.id = p.user_id"""


def select(extra: str = "") -> str:
    return _SELECT.format(extra=extra)


def _enc(*parts) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts, default=str).encode()).decode()


def _dec(cursor: str) -> list:
    try:
        v = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if isinstance(v, list) and len(v) == 2:
            return v
    except Exception:  # noqa: BLE001 – ugyldig cursor behandles som første side
        pass
    raise SnakError(400, "Ugyldig cursor")


async def feed(session: AsyncSession, user: User, *, staff: bool, sort: str = "aktivitet", category: str | None = None, uloeste: bool = False,
               cursor: str | None = None, limit: int = PER_PAGE) -> dict:
    """Én side af feedet med kommentar-forhåndsvisning. Cursor på (sorteringsnøgle, id); fastgjorte opslag kun på første side."""
    if sort not in SORTS:
        raise SnakError(400, "Ukendt sortering")
    cfg = await settings(session)
    key = {"aktivitet": "p.last_activity_at", "nyeste": "p.created_at",
           "populaere": """(SELECT count(*) FROM sn_reactions r WHERE r.kind = 'post' AND r.target_id = p.id AND r.created_at > now() - interval '7 days')
                           + (SELECT count(*) FROM sn_comments k WHERE k.post_id = p.id AND k.status = 'synlig' AND k.created_at > now() - interval '7 days')"""}[sort]
    where = ["p.status = 'synlig'", "p.pinned_at IS NULL"]
    args: dict = {"viewer": user.id, "lim": limit + 1}
    if category:
        where.append("c.slug = :cat")
        args["cat"] = category
    if uloeste:
        where.append("c.har_loesning AND p.solution_comment_id IS NULL")
    if cursor:
        k, i = _dec(cursor)
        if sort == "populaere":
            where.append(f"(({key}), p.id) < (:ck, :ci)")
            args.update(ck=int(k), ci=int(i))
        else:
            where.append(f"({key}, p.id) < (:ck, :ci)")
            args.update(ck=datetime.fromisoformat(str(k)), ci=int(i))
    rows = (await session.execute(text(f"{select(f', ({key}) AS sort_key')} WHERE {' AND '.join(w for w in where if w)} ORDER BY sort_key DESC, p.id DESC LIMIT :lim"),
                                  args)).mappings().all()
    more = len(rows) > limit
    rows = rows[:limit]
    pinned = []
    if not cursor:
        pw = ["p.status = 'synlig'", "p.pinned_at IS NOT NULL"] + (["c.slug = :cat"] if category else [])
        pinned = (await session.execute(text(f"{select()} WHERE {' AND '.join(pw)} ORDER BY p.pinned_at DESC LIMIT 10"),
                                        {"viewer": user.id, **({"cat": category} if category else {})})).mappings().all()
    all_rows = [*pinned, *rows]
    posts = [post_out(r, user.id, staff) for r in all_rows]
    if posts:
        from portal.services import snak_comments

        prev = await snak_comments.previews(session, [p["id"] for p in posts], user.id, staff, cfg["feed_kommentarer"], cfg["feed_svar"])
        react = await snak_reactions.summary(session, "post", [p["id"] for p in posts], user.id)
        for p in posts:
            p["comments"] = prev.get(p["id"], {"items": [], "total_top": 0})
            p["reactions"] = react[p["id"]]
    next_cursor = _enc(str(rows[-1]["sort_key"].isoformat() if sort != "populaere" else rows[-1]["sort_key"]), rows[-1]["id"]) if more and rows else None
    return {"posts": posts, "next": next_cursor}


async def get_post(session: AsyncSession, post_id: int, user: User, *, staff: bool) -> dict:
    r = (await session.execute(text(f"{select()} WHERE p.id = :i"), {"i": post_id, "viewer": user.id})).mappings().first()
    if r is None or r["status"] == "slettet" or (r["status"] == "skjult" and not staff and r["user_id"] != user.id):
        raise SnakError(404, "Opslaget findes ikke")
    out = post_out(r, user.id, staff)
    out["reactions"] = (await snak_reactions.summary(session, "post", [post_id], user.id))[post_id]
    return out


# ---------------------------------------------------------------- opslag: skrivning

async def _ban_and_rate(session: AsyncSession, user: User, cfg: dict) -> None:
    ban = await moderation.active_ban(session, user.id)
    if ban:
        until = f" til {ban['until'][:10]}" if ban["until"] else ""
        raise SnakError(403, f"Du er udelukket fra at skrive{until}: {ban['reason']}")
    key = f"snak:post:rate:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 3600)
    if n > cfg["opslag_pr_time"]:
        raise SnakError(429, "Du har lavet for mange opslag på det seneste – vent lidt")


def _check_text(titel: str | None, body: str, cat: dict) -> tuple[str | None, str]:
    titel = (titel or "").strip() or None
    body = (body or "").strip()
    if cat["kraever_titel"] and not (titel and 5 <= len(titel) <= 200):
        raise SnakError(400, "Skriv en titel på 5–200 tegn")
    if titel and len(titel) > 200:
        raise SnakError(400, "Titlen er for lang (højst 200 tegn)")
    if not body:
        raise SnakError(400, "Skriv noget i opslaget")
    if len(body) > MAX_BODY:
        raise SnakError(400, f"Opslaget er for langt (højst {MAX_BODY} tegn)")
    return titel, body


async def _category(session: AsyncSession, category_id: int, *, staff: bool) -> dict:
    c = (await session.execute(text("SELECT id, slug, navn, kraever_titel, har_loesning, kun_staff, arkiveret FROM sn_categories WHERE id = :i"),
                               {"i": category_id})).mappings().first()
    if c is None or c["arkiveret"]:
        raise SnakError(400, "Vælg en kategori")
    if c["kun_staff"] and not staff:
        raise SnakError(403, "Kun redaktionen og moderatorerne kan skrive i denne kategori")
    return dict(c)


async def follow(session: AsyncSession, user_id: int, post_id: int) -> None:
    """Følg automatisk – men aldrig igen, hvis brugeren selv har valgt «følg ikke» (muted)."""
    await session.execute(text("INSERT INTO sn_follows (post_id, user_id, last_seen_at) VALUES (:p, :u, now()) ON CONFLICT DO NOTHING"), {"p": post_id, "u": user_id})


async def create(session: AsyncSession, user: User, *, staff: bool, category_id: int, titel: str | None, body: str) -> int:
    cfg = await settings(session)
    cat = await _category(session, category_id, staff=staff)
    titel, body = _check_text(titel, body, cat)
    await _ban_and_rate(session, user, cfg)
    rendered = content_render.render(body, "kommentar")
    p = SnPost(category_id=cat["id"], user_id=user.id, titel=titel, body_md=body, body_html=rendered.html, body_text=rendered.text)
    session.add(p)
    await session.flush()
    await follow(session, user.id, p.id)
    await session.commit()
    await publish("snak", {"type": "snak_post", "id": p.id, "category_id": cat["id"]})
    return p.id


async def _own_or_none(session: AsyncSession, post_id: int) -> SnPost:
    p = await session.get(SnPost, post_id)
    if p is None or p.status == "slettet":
        raise SnakError(404, "Opslaget findes ikke")
    return p


async def edit(session: AsyncSession, user: User, post_id: int, *, titel: str | None, body: str) -> None:
    p = await _own_or_none(session, post_id)
    if p.user_id != user.id:
        raise SnakError(403, "Du kan kun rette dine egne opslag")
    cfg = await settings(session)
    if datetime.now(timezone.utc) - p.created_at > timedelta(minutes=cfg["ret_minutter"]):
        raise SnakError(403, "Opslaget kan ikke længere rettes")
    cat = await _category(session, p.category_id, staff=True)
    titel, body = _check_text(titel, body, cat)
    rendered = content_render.render(body, "kommentar")
    p.titel, p.body_md, p.body_html, p.body_text, p.edited_at = titel, body, rendered.html, rendered.text, datetime.now(timezone.utc)
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})


async def remove_own(session: AsyncSession, user: User, post_id: int, *, staff: bool) -> None:
    p = await _own_or_none(session, post_id)
    if p.user_id != user.id and not staff:
        raise SnakError(403, "Du kan kun slette dine egne opslag")
    p.status = "slettet"
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})


async def moderate(session: AsyncSession, moderator: User, post_id: int, action: str, *, reason: str = "", category_id: int | None = None) -> None:
    p = await _own_or_none(session, post_id)
    if action == "skjul":
        p.status, p.hidden_reason, p.hidden_by, p.hidden_at = "skjult", (reason or "").strip()[:500] or "Skjult af en moderator", moderator.id, datetime.now(timezone.utc)
    elif action == "vis":
        p.status, p.hidden_reason, p.hidden_by, p.hidden_at = "synlig", None, None, None
    elif action == "laas":
        p.locked = True
    elif action == "aabn":
        p.locked = False
    elif action == "flyt":
        p.category_id = (await _category(session, category_id or 0, staff=True))["id"]
    else:
        raise SnakError(400, "Ukendt handling")
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})


async def pin(session: AsyncSession, post_id: int, pinned: bool) -> None:
    p = await _own_or_none(session, post_id)
    if pinned:
        cfg = await settings(session)
        n = (await session.execute(text("SELECT count(*) FROM sn_posts WHERE pinned_at IS NOT NULL AND status = 'synlig' AND id <> :i"), {"i": post_id})).scalar() or 0
        if n >= cfg["max_pinned"]:
            raise SnakError(400, f"Der kan højst fastgøres {cfg['max_pinned']} opslag")
    p.pinned_at = datetime.now(timezone.utc) if pinned else None
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})


async def set_follow(session: AsyncSession, user_id: int, post_id: int, on: bool) -> None:
    await _own_or_none(session, post_id)
    await session.execute(text("""INSERT INTO sn_follows (post_id, user_id, muted, last_seen_at) VALUES (:p, :u, :m, now())
                                  ON CONFLICT (post_id, user_id) DO UPDATE SET muted = :m"""), {"p": post_id, "u": user_id, "m": not on})
    await session.commit()


async def register_view(session: AsyncSession, user_id: int, post_id: int) -> None:
    """En visning tælles én gang pr. bruger pr. døgn."""
    ins = (await session.execute(text("""INSERT INTO sn_views (post_id, user_id, dag) SELECT :p, :u, current_date WHERE EXISTS (SELECT 1 FROM sn_posts WHERE id = :p AND status = 'synlig')
                                          ON CONFLICT DO NOTHING RETURNING 1"""), {"p": post_id, "u": user_id})).first()
    if ins:
        await session.execute(text("UPDATE sn_posts SET views = views + 1 WHERE id = :p"), {"p": post_id})
    await session.commit()
