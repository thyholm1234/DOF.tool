"""Snak: opslag, feed, trådside, følg, visninger og kategorier (docs/snak-modul-spec.md §4, §6)."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.models import SnCategory, SnPost, User
from portal.services import avatar, content_render, moderation, snak_attach, snak_notify, snak_reactions
from portal.services.content_media import _url
from portal.services.setting_store import get_setting_dict, put_setting

KEY = "snak"
DEFAULTS: dict = {"feed_kommentarer": 3, "feed_svar": 1, "ret_minutter": 1440, "opslag_pr_time": 6, "kommentarer_pr_minut": 5,
                  "max_billeder_opslag": 10, "max_pinned": 3, "report_threshold": 3, "link_preview": 1}
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
        SELECT c.id, c.slug, c.navn, c.beskrivelse, c.farve, c.ikon, c.sort, c.kraever_titel, c.har_loesning, c.kun_staff, c.arkiveret,
               (SELECT count(*) FROM sn_posts p WHERE p.category_id = c.id AND p.status = 'synlig') AS antal
        FROM sn_categories c {'' if include_archived else 'WHERE NOT c.arkiveret'} ORDER BY c.sort, c.id"""))).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- billeder og kort

MEDIA_KIND = {"post": "snak", "comment": "snakk"}


def media_out(r) -> dict:
    return {"id": r["id"], "url": _url(r["storage_key"]), "thumb": _url(r["thumb_key"] or r["storage_key"]), "medium": _url(r["medium_key"] or r["storage_key"]),
            "width": r["width"], "height": r["height"], "alt": r["alt"] or ""}


async def media_for(session: AsyncSession, kind: str, ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = (await session.execute(text("""SELECT id, owner_id, storage_key, thumb_key, medium_key, width, height, alt FROM content_media
                                          WHERE owner_kind = :k AND owner_id = ANY(:ids) ORDER BY sort, id"""), {"k": MEDIA_KIND[kind], "ids": ids})).mappings().all()
    for r in rows:
        out[r["owner_id"]].append(media_out(r))
    return out


async def media_info(session: AsyncSession, key: str) -> dict:
    """Hører filen til et Snak-opslag eller en kommentar? Giver opslagets status og ejer, så /media/art kan afvise skjulte opslag. Cachet 60 s."""
    ck = "snak:media:" + key
    if hit := await kv().get(ck):
        return json.loads(hit)
    r = (await session.execute(text("""
        SELECT m.owner_kind, m.owner_id, m.created_by, CASE m.owner_kind WHEN 'snak' THEN p.status ELSE cp.status END AS pstatus,
               CASE m.owner_kind WHEN 'snak' THEN p.user_id ELSE cp.user_id END AS puser, c.status AS cstatus, c.user_id AS cuser
        FROM content_media m LEFT JOIN sn_posts p ON m.owner_kind = 'snak' AND p.id = m.owner_id
             LEFT JOIN sn_comments c ON m.owner_kind = 'snakk' AND c.id = m.owner_id LEFT JOIN sn_posts cp ON cp.id = c.post_id
        WHERE m.owner_kind IN ('snak', 'snakk') AND (m.storage_key = :k OR m.thumb_key = :k OR m.medium_key = :k) ORDER BY (m.owner_id IS NULL) LIMIT 1"""), {"k": key})).mappings().first()
    info = {"snak": False}
    if r:
        info = {"snak": True, "attached": r["owner_id"] is not None, "uploader": r["created_by"], "status": r["pstatus"], "owner": r["puser"],
                "cstatus": r["cstatus"], "cowner": r["cuser"]}
    await kv().set(ck, json.dumps(info), ex=60)
    return info


async def forget_media(session: AsyncSession, post_id: int | None = None, comment_id: int | None = None) -> None:
    """Glem den cachede adgangsinfo for et opslags (og dets kommentarers) eller en kommentars billeder, når status eller ejer ændres."""
    rows = (await session.execute(text("""SELECT storage_key, thumb_key, medium_key FROM content_media WHERE
        (owner_kind = 'snak' AND owner_id = :p) OR (owner_kind = 'snakk' AND (owner_id = :c OR owner_id IN (SELECT id FROM sn_comments WHERE post_id = :p)))"""),
        {"p": post_id or 0, "c": comment_id or 0})).all()
    keys = ["snak:media:" + k for r in rows for k in r if k]
    if keys:
        await kv().delete(*keys)


async def finalize(session: AsyncSession, posts: list[dict], staff: bool, flags_on=None) -> None:
    """Tilføj billeder og kontrollér vedhæftede kort på de viste opslag (ét kald pr. slags)."""
    if not posts:
        return
    media = await media_for(session, "post", [p["id"] for p in posts])
    for p in posts:
        if p["status"] != "skjult" or staff or p["mine"]:
            p["images"] = media.get(p["id"], [])
    await snak_attach.refresh(session, posts, flags_on or (lambda _k: True))
    ids = sorted({a for p in posts for a in p["arter"]})
    names = {r[0]: r[1] for r in (await session.execute(text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": ids})).all()} if ids else {}
    for p in posts:
        p["arter_navn"] = [{"artnr": a, "navn": names[a]} for a in p["arter"] if a in names]


async def adopt_media(session: AsyncSession, kind: str, owner_id: int, user_id: int, ids: list[int], *, limit: int) -> None:
    """Medier, der er uploadet før opslaget/kommentaren var gemt (owner_id NULL), bliver dets. Rækkefølgen er listens.
    Medier, der allerede hører til ejeren, men ikke er på listen, slettes."""
    from portal.services import content_media

    ids = list(dict.fromkeys(ids))
    if len(ids) > limit:
        raise SnakError(400, f"Højst {limit} billeder" if limit > 1 else "Højst 1 billede")
    ok = kind and MEDIA_KIND[kind]
    rows = (await session.execute(text("""SELECT id, owner_id FROM content_media WHERE id = ANY(:ids) AND owner_kind = :k
                                          AND ((owner_id IS NULL AND created_by = :u) OR owner_id = :o)"""), {"ids": ids, "k": ok, "u": user_id, "o": owner_id})).all()
    if len(rows) != len(ids):
        raise SnakError(400, "Et af billederne findes ikke mere – upload det igen")
    for i, mid in enumerate(ids):
        await session.execute(text("UPDATE content_media SET owner_id = :o, sort = :s WHERE id = :m"), {"o": owner_id, "s": i, "m": mid})
    old = (await session.execute(text("SELECT id FROM content_media WHERE owner_kind = :k AND owner_id = :o AND NOT (id = ANY(:ids))"), {"k": ok, "o": owner_id, "ids": ids})).all()
    for (mid,) in old:
        m = await content_media.get(session, mid)
        if m is not None:
            await content_media.delete_media(session, m)
    await forget_media(session, owner_id if kind == "post" else None, owner_id if kind == "comment" else None)


async def clean_extras(session: AsyncSession, user: User, arter: list[int] | None, loknr: int | None) -> tuple[list[int], int | None, str | None]:
    """Op til 5 kendte arter og en kendt lokalitet."""
    arter = list(dict.fromkeys(arter or []))
    if len(arter) > 5:
        raise SnakError(400, "Højst 5 arter")
    if arter:
        found = {r[0] for r in (await session.execute(text("SELECT artnr FROM species WHERE artnr = ANY(:a)"), {"a": arter})).all()}
        arter = [a for a in arter if a in found]
    navn = None
    if loknr:
        r = (await session.execute(text("SELECT navn FROM locations WHERE loknr = :l"), {"l": loknr})).first()
        if r is None:
            raise SnakError(400, "Ukendt lokalitet")
        navn = r[0][:200]
    return arter, (loknr or None), navn


# ---------------------------------------------------------------- opslag: læsning

def _author(r) -> dict:
    if r.get("article_id"):  # artiklens opslag: forfatterne (ofte ikke brugere i DOF.tool)
        return {"navn": r["eksempel_navn"] or "Artikler", "artikel": True}
    if r["user_id"] is None:
        return {"navn": r["eksempel_navn"] or "Slettet bruger", "eksempel": True}
    return {"navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"], "user_id": r["user_id"], "avatar": (avatar.urls(r["avatar"]) or {}).get("s")}


def post_out(r, viewer_id: int, staff: bool) -> dict:
    hidden = r["status"] == "skjult"
    mine = r["user_id"] == viewer_id and r["user_id"] is not None
    show = not hidden or staff or mine
    out = {"id": r["id"], "category": {"id": r["category_id"], "slug": r["cat_slug"], "navn": r["cat_navn"], "farve": r["cat_farve"]},
           "author": _author(r), "mine": mine, "titel": r["titel"], "html": r["body_html"] if show else "", "status": r["status"],
           "md": r["body_md"] if mine else None, "locked": r["locked"], "pinned": r["pinned_at"] is not None, "solved": r["solution_comment_id"] is not None, "solution_comment_id": r["solution_comment_id"],
           "har_loesning": r["cat_loesning"], "eksempel": r["eksempel"], "n_comments": r["n_comments"], "n_reactions": r["n_reactions"],
           "views": r["views"], "created_at": _iso(r["created_at"]), "edited_at": _iso(r["edited_at"]), "last_activity_at": _iso(r["last_activity_at"]),
           "arter": r["arter"] or [], "arter_navn": [], "loknr": r["loknr"], "lok_navn": r["lok_navn"], "images": [], "attachment": r["attachment"] if show else None, "following": bool(r["following"]),
           "article_id": r.get("article_id")}
    if hidden:
        out["hidden_reason"] = r["hidden_reason"] if staff or mine else None
    return out


_SELECT = """
    SELECT p.id, p.category_id, p.user_id, p.eksempel_navn, p.titel, p.body_md, p.body_html, p.attachment, p.arter, p.loknr, p.lok_navn, p.status, p.hidden_reason, p.locked,
           p.pinned_at, p.solution_comment_id, p.n_comments, p.n_reactions, p.views, p.last_activity_at, p.eksempel, p.created_at, p.edited_at, p.article_id,
           c.slug AS cat_slug, c.navn AS cat_navn, c.farve AS cat_farve, c.har_loesning AS cat_loesning, u.navn, u.obserkode, u.avatar,
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
               cursor: str | None = None, limit: int = PER_PAGE, flags_on=None) -> dict:
    """Én side af feedet med kommentar-forhåndsvisning. Cursor på (sorteringsnøgle, id); fastgjorte opslag kun på første side."""
    if sort not in SORTS:
        raise SnakError(400, "Ukendt sortering")
    cfg = await settings(session)
    key = {"aktivitet": "p.last_activity_at", "nyeste": "p.created_at", "populaere": "(coalesce(rr.n, 0) + coalesce(kk.n, 0))"}[sort]
    # Populære: reaktioner + kommentarer de seneste 7 dage – to aggregeringer i stedet for en korreleret delforespørgsel pr. opslag
    joins = """ LEFT JOIN (SELECT target_id, count(*) AS n FROM sn_reactions WHERE kind = 'post' AND created_at > now() - interval '7 days' GROUP BY target_id) rr ON rr.target_id = p.id
                LEFT JOIN (SELECT post_id, count(*) AS n FROM sn_comments WHERE status = 'synlig' AND created_at > now() - interval '7 days' GROUP BY post_id) kk ON kk.post_id = p.id""" if sort == "populaere" else ""
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
    rows = (await session.execute(text(f"{select(f', ({key}) AS sort_key')}{joins} WHERE {' AND '.join(w for w in where if w)} ORDER BY sort_key DESC, p.id DESC LIMIT :lim"),
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

        await finalize(session, posts, staff, flags_on)

        prev = await snak_comments.previews(session, [p["id"] for p in posts], user.id, staff, cfg["feed_kommentarer"], cfg["feed_svar"])
        react = await snak_reactions.summary(session, "post", [p["id"] for p in posts], user.id)
        for p in posts:
            p["comments"] = prev.get(p["id"], {"items": [], "total_top": 0})
            p["reactions"] = react[p["id"]]
    next_cursor = _enc(str(rows[-1]["sort_key"].isoformat() if sort != "populaere" else rows[-1]["sort_key"]), rows[-1]["id"]) if more and rows else None
    return {"posts": posts, "next": next_cursor}


async def get_post(session: AsyncSession, post_id: int, user: User, *, staff: bool, flags_on=None) -> dict:
    r = (await session.execute(text(f"{select()} WHERE p.id = :i"), {"i": post_id, "viewer": user.id})).mappings().first()
    if r is None or r["status"] == "slettet" or (r["status"] == "skjult" and not staff and r["user_id"] != user.id):
        raise SnakError(404, "Opslaget findes ikke")
    out = post_out(r, user.id, staff)
    await finalize(session, [out], staff, flags_on)
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


def _check_text(titel: str | None, body: str, cat: dict, *, extra: bool = False) -> tuple[str | None, str]:
    titel = (titel or "").strip() or None
    body = (body or "").strip()
    if cat["kraever_titel"] and not (titel and 5 <= len(titel) <= 200):
        raise SnakError(400, "Skriv en titel på 5–200 tegn")
    if titel and len(titel) > 200:
        raise SnakError(400, "Titlen er for lang (højst 200 tegn)")
    if not body and not extra:
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


async def _attachment(session: AsyncSession, user: User, att: dict | None, flags_on=None) -> dict | None:
    if not att:
        return None
    try:
        return await snak_attach.resolve(session, user.id, str(att.get("type", "")), str(att.get("ref", "")))
    except snak_attach.AttachError as e:
        raise SnakError(e.status, e.message) from e


_ART_TAG = re.compile(r"\[\[art:(\d+)\]\]")
MAX_ARTER = 5


def tagged_species(body: str | None) -> list[int]:
    """Arter, der er tagget med @ i teksten ([[art:N]]), i den rækkefølge de står, uden dubletter."""
    return list(dict.fromkeys(int(n) for n in _ART_TAG.findall(body or "")))


async def add_tagged(session: AsyncSession, user: User, body: str, arter: list[int], att: dict | None, *, old_body: str | None = None) -> tuple[list[int], dict | None]:
    """Arter tagget i teksten kommer med på opslaget: den første bliver det vedhæftede kort, hvis opslaget ikke har et (et kort, forfatteren selv
    har valgt, bevares); de øvrige kommer på art-chipsene (højst 5). Ved ret tæller kun arter, der ikke stod i den gamle tekst, så et kort eller
    en chip, forfatteren har fjernet med vilje, ikke kommer tilbage. Ukendte artsnumre ignoreres."""
    tags = tagged_species(body)
    if old_body is not None:
        old = set(tagged_species(old_body))
        tags = [t for t in tags if t not in old]
    if tags:
        known = {r[0] for r in (await session.execute(text("SELECT artnr FROM species WHERE artnr = ANY(:a)"), {"a": tags})).all()}
        tags = [t for t in tags if t in known]
    if not tags:
        return arter, att
    if att is None:
        att = await _attachment(session, user, {"type": "art", "ref": str(tags[0])})
    card = int(att["ref"]) if att.get("type") == "art" and str(att.get("ref", "")).isdigit() else None
    return list(dict.fromkeys([*arter, *(t for t in tags if t != card)]))[:MAX_ARTER], att


async def create(session: AsyncSession, user: User, *, staff: bool, category_id: int, titel: str | None, body: str, media_ids: list[int] | None = None,
                 attachment: dict | None = None, arter: list[int] | None = None, loknr: int | None = None) -> int:
    cfg = await settings(session)
    cat = await _category(session, category_id, staff=staff)
    media_ids = media_ids or []
    titel, body = _check_text(titel, body, cat, extra=bool(media_ids or attachment))
    await _ban_and_rate(session, user, cfg)
    arter, loknr, lok_navn = await clean_extras(session, user, arter, loknr)
    att = await _attachment(session, user, attachment)
    arter, att = await add_tagged(session, user, body, arter, att)
    rendered = await content_render.render_for(session, body, "kommentar")
    p = SnPost(category_id=cat["id"], user_id=user.id, titel=titel, body_md=body, body_html=rendered.html, body_text=rendered.text, attachment=att,
               arter=arter, loknr=loknr, lok_navn=lok_navn)
    session.add(p)
    await session.flush()
    await adopt_media(session, "post", p.id, user.id, media_ids, limit=cfg["max_billeder_opslag"])
    await follow(session, user.id, p.id)
    await session.commit()
    await publish("snak", {"type": "snak_post", "id": p.id, "category_id": cat["id"]})
    return p.id


async def _own_or_none(session: AsyncSession, post_id: int) -> SnPost:
    p = await session.get(SnPost, post_id)
    if p is None or p.status == "slettet":
        raise SnakError(404, "Opslaget findes ikke")
    return p


async def edit(session: AsyncSession, user: User, post_id: int, *, titel: str | None, body: str, media_ids: list[int] | None = None,
               attachment: dict | None = None, arter: list[int] | None = None, loknr: int | None = None, fields: bool = False,
               category_id: int | None = None, staff: bool = False) -> None:
    """fields=True: billeder, kort, arter og lokalitet rettes også (ellers kun tekst og titel). category_id flytter opslaget
    (samme regler som ved oprettelse); en markeret løsning fjernes, hvis den nye kategori ikke har løsninger."""
    p = await _own_or_none(session, post_id)
    if p.user_id != user.id:
        raise SnakError(403, "Du kan kun rette dine egne opslag")
    cfg = await settings(session)
    if datetime.now(timezone.utc) - p.created_at > timedelta(minutes=cfg["ret_minutter"]):
        raise SnakError(403, "Opslaget kan ikke længere rettes")
    if category_id is not None and category_id != p.category_id:
        cat = await _category(session, category_id, staff=staff)
        p.category_id = cat["id"]
        if not cat["har_loesning"]:
            p.solution_comment_id = None
    else:
        cat = await _category(session, p.category_id, staff=True)
    if fields:
        ids = media_ids or []
        titel, body = _check_text(titel, body, cat, extra=bool(ids or attachment))
        arter, p_loknr, p.lok_navn = await clean_extras(session, user, arter, loknr)
        p.loknr = p_loknr
        att = await _attachment(session, user, attachment) if (attachment or None) != ({"type": p.attachment.get("type"), "ref": p.attachment.get("ref")} if p.attachment else None) else p.attachment
        p.arter, p.attachment = await add_tagged(session, user, body, arter, att, old_body=p.body_md)
        await adopt_media(session, "post", p.id, user.id, ids, limit=cfg["max_billeder_opslag"])
    else:
        n_media = (await session.execute(text("SELECT count(*) FROM content_media WHERE owner_kind = 'snak' AND owner_id = :o"), {"o": post_id})).scalar() or 0
        titel, body = _check_text(titel, body, cat, extra=bool(p.attachment) or n_media > 0)
        p.arter, p.attachment = await add_tagged(session, user, body, list(p.arter or []), p.attachment, old_body=p.body_md)
    rendered = await content_render.render_for(session, body, "kommentar")
    p.titel, p.body_md, p.body_html, p.body_text, p.edited_at = titel, body, rendered.html, rendered.text, datetime.now(timezone.utc)
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})


async def set_solution(session: AsyncSession, user: User, post_id: int, comment_id: int | None, *, staff: bool) -> None:
    """Forfatteren (eller en moderator) markerer en kommentar som løsning – eller fjerner markeringen (comment_id None)."""
    p = await _own_or_none(session, post_id)
    if p.user_id != user.id and not staff:
        raise SnakError(403, "Kun forfatteren eller en moderator kan markere en løsning")
    cat = await _category(session, p.category_id, staff=True)
    if not cat["har_loesning"]:
        raise SnakError(400, "Kategorien har ikke løsninger")
    if comment_id is not None:
        ok = (await session.execute(text("SELECT 1 FROM sn_comments WHERE id = :c AND post_id = :p AND status = 'synlig'"), {"c": comment_id, "p": post_id})).first()
        if not ok:
            raise SnakError(400, "Kommentaren findes ikke på opslaget")
    p.solution_comment_id = comment_id
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})
    if comment_id is not None:
        await snak_notify.solution_marked(session, user, post_id, comment_id)


async def remove_own(session: AsyncSession, user: User, post_id: int, *, staff: bool) -> None:
    p = await _own_or_none(session, post_id)
    if p.user_id != user.id and not staff:
        raise SnakError(403, "Du kan kun slette dine egne opslag")
    p.status = "slettet"
    await session.commit()
    await forget_media(session, post_id)
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
    elif action == "afvis_anmeldelser":
        if p.status == "skjult" and p.hidden_by is None:  # skjult automatisk af anmeldelserne
            p.status, p.hidden_reason, p.hidden_at = "synlig", None, None
    else:
        raise SnakError(400, "Ukendt handling")
    if action in ("skjul", "vis", "afvis_anmeldelser"):
        await resolve_reports(session, "post", post_id, moderator.id)
    await session.commit()
    await forget_media(session, post_id)
    await publish("snak", {"type": "snak_mod", "kind": "post", "id": post_id})
    if action == "skjul":
        await snak_notify.hidden(session, "post", post_id, p.hidden_reason)


async def resolve_reports(session: AsyncSession, kind: str, target_id: int, moderator_id: int) -> None:
    """Anmeldelserne af et opslag/en kommentar er behandlet (moderatoren har skjult, vist eller afvist dem)."""
    await session.execute(text("UPDATE sn_reports SET handled_at = now(), handled_by = :m WHERE kind = :k AND target_id = :t AND handled_at IS NULL"),
                          {"m": moderator_id, "k": kind, "t": target_id})


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
    """En visning tælles én gang pr. bruger pr. døgn. At åbne tråden tæller også som «set» for dem, der følger den (nye kommentarer i Mine)."""
    await session.execute(text("UPDATE sn_follows SET last_seen_at = now() WHERE post_id = :p AND user_id = :u"), {"p": post_id, "u": user_id})
    ins = (await session.execute(text("""INSERT INTO sn_views (post_id, user_id, dag) SELECT :p, :u, current_date WHERE EXISTS (SELECT 1 FROM sn_posts WHERE id = :p AND status = 'synlig')
                                          ON CONFLICT DO NOTHING RETURNING 1"""), {"p": post_id, "u": user_id})).first()
    if ins:
        await session.execute(text("UPDATE sn_posts SET views = views + 1 WHERE id = :p"), {"p": post_id})
    await session.commit()


# ---------------------------------------------------------------- søg, mine og sidepanel (fase 4)

HL = ("\x02", "\x03")  # markering af træf i uddraget; frontend bygger <mark> selv, så brugerens tekst aldrig tolkes som HTML


def prefix_query(q: str) -> str:
    """Søgeteksten som tsquery, hvor hvert ord matcher ordbegyndelser: «rubin» finder Rubinnattergal. Kun bogstaver og tal kommer med."""
    words = re.findall(r"[^\W_]+", q.lower())[:12]
    return " & ".join(f"{w}:*" for w in words)


async def search(session: AsyncSession, *, q: str, category: str | None = None, art: int | None = None, days: int | None = None, uloeste: bool = False,
                 billeder: bool = False, offset: int = 0, limit: int = 20) -> dict:
    """Fritekst i titel, tekst og kommentarer (dansk tsvector, ordbegyndelser). Kun synlige opslag/kommentarer; træf i en kommentar peger på kommentaren."""
    q = (q or "").strip()
    if len(q) < 2:
        raise SnakError(400, "Skriv mindst to bogstaver")
    if not (tsq := prefix_query(q)):
        return {"rows": [], "more": False}
    where, args = ["p.status = 'synlig'"], {"q": tsq, "lim": limit + 1, "off": offset, "a": HL[0], "b": HL[1]}
    if category:
        where.append("c.slug = :cat")
        args["cat"] = category
    if art:
        where.append("p.arter @> ARRAY[CAST(:art AS int)]")
        args["art"] = art
    if days:
        where.append("p.created_at > now() - make_interval(days => :days)")
        args["days"] = days
    if uloeste:
        where.append("c.har_loesning AND p.solution_comment_id IS NULL")
    if billeder:
        where.append("EXISTS (SELECT 1 FROM content_media m WHERE m.owner_kind = 'snak' AND m.owner_id = p.id)")
    w = " AND ".join(where)
    rows = (await session.execute(text(f"""
        WITH query AS (SELECT to_tsquery('danish', :q) AS q), hits AS (
          SELECT 'post' AS kind, p.id AS post_id, NULL::int AS comment_id, ts_rank(p.search, query.q) AS rank, p.created_at, p.user_id, p.eksempel_navn,
                 coalesce(p.titel, '') || ' ' || coalesce(p.body_text, '') AS txt
          FROM sn_posts p JOIN sn_categories c ON c.id = p.category_id, query WHERE {w} AND p.search @@ query.q
          UNION ALL
          SELECT 'comment', p.id, k.id, ts_rank(k.search, query.q), k.created_at, k.user_id, k.eksempel_navn, coalesce(k.body_text, '')
          FROM sn_comments k JOIN sn_posts p ON p.id = k.post_id JOIN sn_categories c ON c.id = p.category_id, query WHERE {w} AND k.status = 'synlig' AND k.search @@ query.q)
        SELECT h.kind, h.post_id, h.comment_id, h.created_at, p.titel, c.navn AS kategori, c.slug, c.farve, coalesce(u.navn, u.obserkode, h.eksempel_navn) AS navn,
               ts_headline('danish', h.txt, query.q, 'MaxWords=34, MinWords=14, ShortWord=2, StartSel=' || :a || ', StopSel=' || :b) AS uddrag
        FROM (SELECT * FROM hits ORDER BY rank DESC, created_at DESC LIMIT :lim OFFSET :off) h
        JOIN sn_posts p ON p.id = h.post_id JOIN sn_categories c ON c.id = p.category_id LEFT JOIN users u ON u.id = h.user_id, query
        ORDER BY h.rank DESC, h.created_at DESC"""), args)).mappings().all()
    return {"rows": [{**dict(r), "created_at": _iso(r["created_at"])} for r in rows[:limit]], "more": len(rows) > limit}


async def mine(session: AsyncSession, user: User) -> dict:
    """«Mine opslag» og «Tråde jeg følger» med antal nye kommentarer siden brugeren sidst åbnede tråden."""
    own = (await session.execute(text("""
        SELECT p.id, p.titel, left(p.body_text, 120) AS uddrag, p.status, p.n_comments, p.n_reactions, p.created_at, c.navn AS kategori, c.slug, c.farve
        FROM sn_posts p JOIN sn_categories c ON c.id = p.category_id WHERE p.user_id = :u AND p.status IN ('synlig', 'skjult') ORDER BY p.created_at DESC LIMIT 50"""), {"u": user.id})).mappings().all()
    followed = (await session.execute(text("""
        SELECT p.id, p.titel, left(p.body_text, 120) AS uddrag, p.n_comments, p.last_activity_at, c.navn AS kategori, c.slug, c.farve,
               (SELECT count(*) FROM sn_comments k WHERE k.post_id = p.id AND k.status = 'synlig' AND k.user_id IS DISTINCT FROM :u
                AND k.created_at > coalesce(f.last_seen_at, f.created_at)) AS nye
        FROM sn_follows f JOIN sn_posts p ON p.id = f.post_id JOIN sn_categories c ON c.id = p.category_id
        WHERE f.user_id = :u AND NOT f.muted AND p.status = 'synlig' AND p.user_id IS DISTINCT FROM :u ORDER BY nye DESC, p.last_activity_at DESC LIMIT 50"""), {"u": user.id})).mappings().all()
    fix = lambda rows, *cols: [{**dict(r), **{c: _iso(r[c]) for c in cols}} for r in rows]  # noqa: E731
    return {"opslag": fix(own, "created_at"), "foelger": fix(followed, "last_activity_at")}


async def panel(session: AsyncSession, since: datetime | None) -> dict:
    """Sidepanelet: kategorier med antal opslag med aktivitet siden sidste besøg, uløste bestemmelser og de mest aktive tråde i ugen."""
    cats = (await session.execute(text("""
        SELECT c.id, c.slug, c.navn, c.farve,
               (SELECT count(*) FROM sn_posts p WHERE p.category_id = c.id AND p.status = 'synlig' AND CAST(:since AS timestamptz) IS NOT NULL AND p.last_activity_at > CAST(:since AS timestamptz)) AS nye
        FROM sn_categories c WHERE NOT c.arkiveret ORDER BY c.sort, c.id"""), {"since": since})).mappings().all()
    open_ = (await session.execute(text("""
        SELECT p.id, coalesce(p.titel, left(p.body_text, 70)) AS titel, p.n_comments, p.created_at FROM sn_posts p JOIN sn_categories c ON c.id = p.category_id
        WHERE p.status = 'synlig' AND c.har_loesning AND p.solution_comment_id IS NULL ORDER BY p.created_at DESC LIMIT 5"""))).mappings().all()
    active = (await session.execute(text("""
        SELECT * FROM (SELECT p.id, coalesce(p.titel, left(p.body_text, 70)) AS titel, p.n_comments,
               (SELECT count(*) FROM sn_comments k WHERE k.post_id = p.id AND k.status = 'synlig' AND k.created_at > now() - interval '7 days')
               + (SELECT count(*) FROM sn_reactions r WHERE r.kind = 'post' AND r.target_id = p.id AND r.created_at > now() - interval '7 days') AS score
               FROM sn_posts p WHERE p.status = 'synlig' AND p.last_activity_at > now() - interval '7 days') t WHERE score > 0 ORDER BY score DESC, id DESC LIMIT 5"""))).mappings().all()
    return {"kategorier": [dict(r) for r in cats], "uloeste": [{**dict(r), "created_at": _iso(r["created_at"])} for r in open_], "aktive": [dict(r) for r in active]}
