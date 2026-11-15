"""Reaktioner på Snak-opslag og -kommentarer: 👍 ❤️ 😂 😮 😢 😡 (docs/snak-modul-spec.md §2.9, §3).

Én reaktion pr. bruger pr. mål. ``n_reactions`` på opslag/kommentar holdes opdateret i samme transaktion."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.models import User

# type → (emoji, dansk navn). Rækkefølgen er vælgerens rækkefølge.
TYPES: dict[str, tuple[str, str]] = {
    "like": ("👍", "Synes godt om"), "love": ("❤️", "Elsker"), "haha": ("😂", "Haha"),
    "wow": ("😮", "Wow"), "sad": ("😢", "Ked af det"), "angry": ("😡", "Vred"),
}
KINDS = {"post": "sn_posts", "comment": "sn_comments"}
RATE_PER_MINUTE = 60
PAGE = 50


class ReactionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def empty() -> dict:
    return {"count": 0, "counts": {}, "mine": None, "top": [], "names": []}


async def summary(session: AsyncSession, kind: str, ids: list[int], user_id: int | None) -> dict[int, dict]:
    """{mål-id: {count, counts, mine, top (op til 3 typer), names (de to første)}} – to forespørgsler for alle ids."""
    out = {i: empty() for i in ids}
    if not ids:
        return out
    rows = (await session.execute(text("""
        SELECT target_id, type, count(*) AS n FROM sn_reactions WHERE kind = :k AND target_id = ANY(:ids) GROUP BY target_id, type"""),
        {"k": kind, "ids": ids})).all()
    for tid, typ, n in rows:
        d = out[tid]
        d["counts"][typ] = n
        d["count"] += n
    for d in out.values():
        d["top"] = [t for t, _ in sorted(d["counts"].items(), key=lambda kv_: (-kv_[1], list(TYPES).index(kv_[0])))[:3]]
    extra = (await session.execute(text("""
        SELECT r.target_id, r.user_id = :me AS mine, coalesce(u.navn, u.obserkode) AS navn
        FROM sn_reactions r JOIN users u ON u.id = r.user_id
        WHERE r.kind = :k AND r.target_id = ANY(:ids) ORDER BY r.created_at DESC"""),
        {"k": kind, "ids": ids, "me": user_id or -1})).all()
    mine_types = (await session.execute(text("SELECT target_id, type FROM sn_reactions WHERE kind = :k AND target_id = ANY(:ids) AND user_id = :me"),
                                        {"k": kind, "ids": ids, "me": user_id or -1})).all()
    for tid, typ in mine_types:
        out[tid]["mine"] = typ
    for tid, _mine, navn in extra:
        if len(out[tid]["names"]) < 2:
            out[tid]["names"].append(navn)
    return out


async def _target(session: AsyncSession, kind: str, target_id: int, *, staff: bool) -> dict:
    """Målet, hvis det findes og er synligt for brugeren (skjult/slettet opslag og kommentarer kan ikke reageres på)."""
    if kind not in KINDS:
        raise ReactionError(400, "Ugyldigt mål")
    if kind == "post":
        row = (await session.execute(text("SELECT id, status FROM sn_posts WHERE id = :i"), {"i": target_id})).mappings().first()
        post_id, status = (row["id"], row["status"]) if row else (None, None)
    else:
        row = (await session.execute(text("""SELECT c.id, c.status, p.id AS post_id, p.status AS pstatus FROM sn_comments c
                                              JOIN sn_posts p ON p.id = c.post_id WHERE c.id = :i"""), {"i": target_id})).mappings().first()
        post_id = row["post_id"] if row else None
        status = "synlig" if row and row["status"] == "synlig" and row["pstatus"] == "synlig" else "skjult"
    if row is None or status != "synlig":
        raise ReactionError(404, "Opslaget eller kommentaren findes ikke")
    return {"post_id": post_id}


async def set_reaction(session: AsyncSession, user: User, kind: str, target_id: int, rtype: str | None, *, staff: bool = False) -> dict:
    """Sæt, skift (ny type) eller fjern (rtype None, eller samme type som den, der allerede er sat) brugerens reaktion."""
    from portal.services import moderation

    if rtype is not None and rtype not in TYPES:
        raise ReactionError(422, "Ukendt reaktion")
    t = await _target(session, kind, target_id, staff=staff)
    if rtype is not None and (ban := await moderation.active_ban(session, user.id)):
        raise ReactionError(403, f"Du er udelukket: {ban['reason']}")
    key = f"snak:react:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 60)
    if n > RATE_PER_MINUTE:
        raise ReactionError(429, "Du reagerer for hurtigt – vent lidt")
    table = KINDS[kind]
    cur = (await session.execute(text("SELECT id, type FROM sn_reactions WHERE user_id = :u AND kind = :k AND target_id = :t FOR UPDATE"),
                                 {"u": user.id, "k": kind, "t": target_id})).first()
    delta = 0
    if cur is not None and (rtype is None or cur[1] == rtype):
        await session.execute(text("DELETE FROM sn_reactions WHERE id = :i"), {"i": cur[0]})
        delta = -1
    elif cur is not None:
        await session.execute(text("UPDATE sn_reactions SET type = :ty, changed_at = now() WHERE id = :i"), {"ty": rtype, "i": cur[0]})
    elif rtype is not None:
        ins = (await session.execute(text("""INSERT INTO sn_reactions (user_id, kind, target_id, type) VALUES (:u, :k, :t, :ty)
                                              ON CONFLICT (user_id, kind, target_id) DO NOTHING RETURNING id"""),
                                     {"u": user.id, "k": kind, "t": target_id, "ty": rtype})).first()
        if ins is not None:
            delta = 1
        else:  # samtidigt kald nåede først: skift i stedet
            await session.execute(text("UPDATE sn_reactions SET type = :ty, changed_at = now() WHERE user_id = :u AND kind = :k AND target_id = :t"),
                                  {"ty": rtype, "u": user.id, "k": kind, "t": target_id})
    if delta:
        await session.execute(text(f"UPDATE {table} SET n_reactions = greatest(n_reactions + :d, 0) WHERE id = :i"), {"d": delta, "i": target_id})
    await session.commit()
    s = (await summary(session, kind, [target_id], user.id))[target_id]
    await publish("snak", {"type": "snak_reaction", "kind": kind, "id": target_id, "post_id": t["post_id"], "count": s["count"], "counts": s["counts"]})
    return s


async def who(session: AsyncSession, kind: str, target_id: int, rtype: str | None, offset: int = 0) -> dict:
    """Hvem reagerede (side à 50), evt. kun med én type; counts bruges til fanerne."""
    if kind not in KINDS:
        raise ReactionError(400, "Ugyldigt mål")
    if rtype is not None and rtype not in TYPES:
        raise ReactionError(422, "Ukendt reaktion")
    rows = (await session.execute(text("""
        SELECT u.id AS user_id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode, r.type, r.created_at AS at
        FROM sn_reactions r JOIN users u ON u.id = r.user_id
        WHERE r.kind = :k AND r.target_id = :t AND (CAST(:ty AS text) IS NULL OR r.type = :ty)
        ORDER BY r.created_at DESC LIMIT :lim OFFSET :off"""), {"k": kind, "t": target_id, "ty": rtype, "lim": PAGE + 1, "off": offset})).mappings().all()
    counts = {t: n for t, n in (await session.execute(text("SELECT type, count(*) FROM sn_reactions WHERE kind = :k AND target_id = :t GROUP BY type"),
                                                       {"k": kind, "t": target_id})).all()}
    return {"users": [dict(r) for r in rows[:PAGE]], "more": len(rows) > PAGE, "counts": counts, "count": sum(counts.values())}
