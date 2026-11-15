"""Snak: anmeld og moderationskø (docs/snak-modul-spec.md §2.8). Efter-moderation: alt vises straks, brugere anmelder, moderatorer har en kø.

Når antallet åbne anmeldelser når tærsklen (indstillingen ``report_threshold``), skjules indholdet automatisk – uden ``hidden_by`` – til en moderator har
set på det; «Afvis anmeldelser» viser det automatisk skjulte indhold igen."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.models import User
from portal.services import snak

REASONS = {"chikane", "spam", "forkert", "andet"}
TABS = {"anmeldte": "n_reports > 0 AND status <> 'slettet'", "skjulte": "status = 'skjult'", "seneste": "created_at > now() - interval '7 days'"}
PAGE = 50


async def report(session: AsyncSession, user: User, kind: str, target_id: int, reason: str, note: str) -> bool:
    """Anmeld et opslag eller en kommentar. True, hvis indholdet blev skjult automatisk."""
    if kind not in ("post", "comment"):
        raise snak.SnakError(400, "Ugyldigt mål")
    if reason not in REASONS:
        raise snak.SnakError(400, "Vælg en årsag")
    if kind == "post":
        r = (await session.execute(text("SELECT user_id, status, id AS post_id FROM sn_posts WHERE id = :i"), {"i": target_id})).mappings().first()
    else:
        r = (await session.execute(text("""SELECT c.user_id, CASE WHEN p.status = 'synlig' THEN c.status ELSE p.status END AS status, c.post_id
                                           FROM sn_comments c JOIN sn_posts p ON p.id = c.post_id WHERE c.id = :i"""), {"i": target_id})).mappings().first()
    if r is None or r["status"] == "slettet":
        raise snak.SnakError(404, "Opslaget eller kommentaren findes ikke")
    if r["user_id"] == user.id:
        raise snak.SnakError(400, "Du kan ikke anmelde dit eget indhold")
    key = f"snak:report:rate:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 3600)
    if n > 30:
        raise snak.SnakError(429, "Du har anmeldt meget på det seneste – vent lidt")
    await session.execute(text("""INSERT INTO sn_reports (kind, target_id, user_id, reason, note) VALUES (:k, :t, :u, :r, :n)
                                  ON CONFLICT (kind, target_id, user_id) DO NOTHING"""), {"k": kind, "t": target_id, "u": user.id, "r": reason, "n": (note or "")[:500]})
    open_ = (await session.execute(text("SELECT count(*) FROM sn_reports WHERE kind = :k AND target_id = :t AND handled_at IS NULL"), {"k": kind, "t": target_id})).scalar() or 0
    cfg = await snak.settings(session)
    auto = bool(cfg["report_threshold"]) and open_ >= cfg["report_threshold"] and r["status"] == "synlig"
    if auto:
        tbl = "sn_posts" if kind == "post" else "sn_comments"
        await session.execute(text(f"UPDATE {tbl} SET status = 'skjult', hidden_reason = 'Anmeldt – afventer moderator', hidden_by = NULL, hidden_at = now() WHERE id = :i"), {"i": target_id})
        if kind == "comment":
            await session.execute(text("UPDATE sn_posts SET n_comments = greatest(n_comments - 1, 0) WHERE id = :p"), {"p": r["post_id"]})
    await session.commit()
    if auto:
        await snak.forget_media(session, post_id=target_id if kind == "post" else None, comment_id=target_id if kind == "comment" else None)
        await publish("snak", {"type": "snak_mod", "kind": kind, "id": target_id, "post_id": r["post_id"]})
    return auto


async def queue(session: AsyncSession, tab: str, limit: int = PAGE, offset: int = 0) -> list[dict]:
    """tab: anmeldte (åbne anmeldelser, flest først) · skjulte · seneste (7 dage). Opslag og kommentarer i én liste."""
    where = TABS.get(tab)
    if where is None:
        raise snak.SnakError(400, "Ukendt fane")
    rows = (await session.execute(text(f"""
        SELECT * FROM (
          SELECT i.*, u.navn, u.obserkode, rp.n AS n_reports, rp.reports FROM (
            SELECT 'post' AS kind, p.id, p.id AS post_id, p.user_id, p.eksempel_navn, p.titel, p.body_md AS body, p.status, p.hidden_reason, p.hidden_by, p.locked, p.created_at, c.navn AS kategori
            FROM sn_posts p JOIN sn_categories c ON c.id = p.category_id WHERE p.status <> 'slettet'
            UNION ALL
            SELECT 'comment', k.id, k.post_id, k.user_id, k.eksempel_navn, p.titel, k.body_md, k.status, k.hidden_reason, k.hidden_by, p.locked, k.created_at, c.navn
            FROM sn_comments k JOIN sn_posts p ON p.id = k.post_id JOIN sn_categories c ON c.id = p.category_id WHERE k.status <> 'slettet' AND p.status <> 'slettet') i
          LEFT JOIN users u ON u.id = i.user_id
          LEFT JOIN LATERAL (SELECT count(*) AS n, coalesce(jsonb_agg(jsonb_build_object('reason', r.reason, 'note', r.note, 'af', coalesce(ru.navn, ru.obserkode)) ORDER BY r.created_at), '[]'::jsonb) AS reports
                             FROM sn_reports r JOIN users ru ON ru.id = r.user_id WHERE r.kind = i.kind AND r.target_id = i.id AND r.handled_at IS NULL) rp ON true) t
        WHERE {where} ORDER BY (n_reports > 0) DESC, n_reports DESC, created_at DESC, id DESC LIMIT :lim OFFSET :off"""), {"lim": limit, "off": offset})).mappings().all()
    return [{"kind": r["kind"], "id": r["id"], "post_id": r["post_id"], "titel": r["titel"], "kategori": r["kategori"], "user_id": r["user_id"],
             "navn": r["eksempel_navn"] if r["user_id"] is None else (r["navn"] or r["obserkode"]), "obserkode": r["obserkode"], "body": (r["body"] or "")[:600],
             "status": r["status"], "hidden_reason": r["hidden_reason"], "auto": r["status"] == "skjult" and r["hidden_by"] is None, "locked": r["locked"],
             "n_reports": r["n_reports"], "reports": r["reports"], "created_at": r["created_at"].isoformat()} for r in rows]
