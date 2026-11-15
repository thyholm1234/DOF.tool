"""Artiklernes Snak-opslag: hver publiceret artikel har ét opslag i kategorien «Artikler og læsning» (sn_posts.article_id).

* Titlen er artiklens titel, teksten er redaktørens tekst til opslaget (art_articles.snak_tekst) eller et uddrag af artiklens første tekst,
  og opslaget har artiklen som vedhæftet kort. Afsenderen er artiklens forfattere (ingen bruger).
* Opslaget dateres til artiklen (netfugl-arkivets artikler til deres dato, nye til publiceringen), så gamle artikler ligger langt nede i
  feedet og kun kommer op, når nogen kommenterer.
* Opslagets kommentarer er artiklens kommentarspor: de skrives og læses både på opslaget og på artikelsiden. Arkivets kommentarer
  (art_comments, netfugl.dk) bliver på artiklen og indgår ikke i Snak.
* Er artiklen ikke (længere) publiceret, skjules opslaget; publiceres den igen, vises det igen – medmindre en moderator har skjult det.

``sync`` kaldes, når en artikel gemmes; ``backfill`` (workerens artikeljob) opretter opslag til publicerede artikler, der mangler et –
de netop planlagt-publicerede og nye fra importen, og første gang hele arkivet."""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services import content_render, snak, snak_attach

log = logging.getLogger("portal.snak")

CATEGORY = "artikler"
EXCERPT_LEN = 300
MAX_TEXT = 2000
MAX_ARTER = 5
HIDDEN_REASON = "Artiklen er ikke publiceret"
BATCH = 200

_ARTICLE = """
    SELECT a.id, a.title, a.lead, a.body_text, a.snak_tekst, a.status, a.source, a.dato, a.published_at, a.comments_open,
           (SELECT string_agg(au.navn, ', ' ORDER BY x.sort, au.id) FROM art_article_authors x JOIN art_authors au ON au.id = x.author_id
             WHERE x.article_id = a.id) AS forfattere,
           (SELECT coalesce(array_agg(s.artnr ORDER BY s.first_pos NULLS LAST, s.hits DESC), '{}') FROM
              (SELECT artnr, first_pos, hits FROM art_species WHERE article_id = a.id AND source NOT IN ('fravalgt', 'ocr')
                ORDER BY first_pos NULLS LAST, hits DESC LIMIT :n_arter) s) AS arter
    FROM art_articles a WHERE a.id = :i"""


def excerpt(body_text: str | None, lead: str | None = None, n: int = EXCERPT_LEN) -> str:
    """Uddrag af artiklens første tekst (manchetten, hvis der ingen brødtekst er): højst n tegn, skåret ved et ord og afsluttet med «…»."""
    src = re.sub(r"\s+", " ", (body_text or "").strip()) or re.sub(r"\s+", " ", (lead or "").strip())
    if len(src) <= n:
        return src
    cut = src[:n + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut[n // 2:] else src[:n]
    return cut.rstrip(" ,;:–-.") + " …"


def _author_name(forfattere: str | None) -> str:
    names = (forfattere or "").strip() or "Artikler"
    return names if len(names) <= 80 else names[:77].rsplit(", ", 1)[0] + " m.fl."


def _dated(a) -> datetime:
    """Netfugl-arkivets artikler dateres til artiklens dato (published_at er importtidspunktet), nye til publiceringen."""
    if a["source"] == "netfugl":
        return a["dato"] or a["published_at"]
    return a["published_at"] or a["dato"]


async def _body(session: AsyncSession, a) -> tuple[str, str, str, list[int]]:
    """(body_md, body_html, body_text, arter tagget i teksten). Redaktørens tekst er markdown som andre opslag; uddraget er ren tekst."""
    own = (a["snak_tekst"] or "").strip()[:MAX_TEXT]
    if own:
        r = await content_render.render_for(session, own, "kommentar")
        return own, r.html, r.text, snak.tagged_species(own)
    ex = excerpt(a["body_text"], a["lead"])
    return ex, f"<p>{htmllib.escape(ex)}</p>" if ex else "", ex, []


async def sync(session: AsyncSession, article_id: int) -> int | None:
    """Opret, ret, skjul eller vis artiklens opslag, så det svarer til artiklen. Returnerer opslagets id (None: intet opslag). Committer ikke."""
    a = (await session.execute(text(_ARTICLE), {"i": article_id, "n_arter": MAX_ARTER})).mappings().first()
    post = (await session.execute(text("SELECT id, status, hidden_reason, hidden_by, attachment FROM sn_posts WHERE article_id = :i"), {"i": article_id})).mappings().first()
    published = a is not None and a["status"] == "publiceret"
    if a is None or (post is None and not published):
        return post["id"] if post else None
    body_md, body_html, body_text, tagged = await _body(session, a)
    arter = list(dict.fromkeys([*tagged, *(a["arter"] or [])]))[:MAX_ARTER]
    attachment = None
    if published:
        try:
            attachment = await snak_attach.resolve(session, 0, "artikel", str(article_id))
        except snak_attach.AttachError:
            attachment = None
    vals = {"titel": a["title"][:200], "md": body_md, "html": body_html, "txt": body_text, "navn": _author_name(a["forfattere"]), "arter": arter,
            "locked": not a["comments_open"], "aid": article_id}
    if post is None:
        cat = (await session.execute(text("SELECT id FROM sn_categories WHERE slug = :s"), {"s": CATEGORY})).scalar()
        if cat is None:
            log.warning("Snak: kategorien %r findes ikke – artiklernes opslag oprettes ikke", CATEGORY)
            return None
        at = _dated(a)
        return (await session.execute(text("""
            INSERT INTO sn_posts (category_id, user_id, eksempel_navn, titel, body_md, body_html, body_text, attachment, arter, locked, article_id,
                                  created_at, last_activity_at)
            VALUES (:cat, NULL, :navn, :titel, :md, :html, :txt, CAST(:att AS jsonb), :arter, :locked, :aid, :at, :at)
            ON CONFLICT (article_id) WHERE article_id IS NOT NULL DO NOTHING RETURNING id"""),
            {**vals, "cat": cat, "att": json.dumps(attachment) if attachment else None, "at": at})).scalar()
    status, reason = post["status"], post["hidden_reason"]
    if published and status == "skjult" and reason == HIDDEN_REASON and post["hidden_by"] is None:
        status, reason = "synlig", None
    elif not published and status == "synlig":
        status, reason = "skjult", HIDDEN_REASON
    await session.execute(text("""
        UPDATE sn_posts SET titel = :titel, body_md = :md, body_html = :html, body_text = :txt, eksempel_navn = :navn, arter = :arter, locked = :locked,
               attachment = coalesce(CAST(:att AS jsonb), attachment), status = :status, hidden_reason = :reason,
               hidden_at = CASE WHEN CAST(:status AS varchar) = 'skjult' THEN coalesce(hidden_at, now()) END
        WHERE id = :id"""), {**vals, "att": json.dumps(attachment) if attachment else None, "status": status, "reason": reason, "id": post["id"]})
    return post["id"]


async def backfill(session: AsyncSession, limit: int | None = None) -> int:
    """Opslag til publicerede artikler, der mangler et (i portioner, hver committet for sig). Returnerer antallet af nye opslag."""
    await _move_follows(session)
    made = 0
    while True:
        ids = [r[0] for r in (await session.execute(text("""
            SELECT a.id FROM art_articles a WHERE a.status = 'publiceret' AND NOT EXISTS (SELECT 1 FROM sn_posts p WHERE p.article_id = a.id)
            ORDER BY a.id LIMIT :n"""), {"n": BATCH})).all()]
        if not ids:
            break
        for aid in ids:
            if await sync(session, aid) is None:
                await session.rollback()
                return made  # kategorien mangler – prøv igen næste gang
            made += 1
        await session.commit()
        if limit is not None and made >= limit:
            break
    if made:
        log.info("Snak: %d opslag oprettet til artikler", made)
    return made


async def _move_follows(session: AsyncSession) -> None:
    """«Følg artikel» lå før i art_subscriptions; nu følger man artiklens opslag (sn_follows). Flyttes én gang, når opslaget findes."""
    moved = (await session.execute(text("""
        WITH m AS (DELETE FROM art_subscriptions s USING sn_posts p WHERE s.kind = 'artikel' AND p.article_id = s.ref RETURNING s.user_id, p.id AS post_id, s.muted)
        INSERT INTO sn_follows (post_id, user_id, muted) SELECT post_id, user_id, muted FROM m ON CONFLICT (post_id, user_id) DO NOTHING"""))).rowcount
    await session.commit()
    if moved:
        log.info("Snak: %d «følg artikel» flyttet til artiklernes opslag", moved)


async def post_id(session: AsyncSession, article_id: int) -> int | None:
    return (await session.execute(text("SELECT id FROM sn_posts WHERE article_id = :i AND status <> 'slettet'"), {"i": article_id})).scalar()


async def article_of(session: AsyncSession, post_id_: int) -> int | None:
    return (await session.execute(text("SELECT article_id FROM sn_posts WHERE id = :i"), {"i": post_id_})).scalar()
