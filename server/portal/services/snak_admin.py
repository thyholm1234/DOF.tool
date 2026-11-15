"""Snak → Admin: kategorier, eksempler og statistik (docs/snak-modul-spec.md §2.7). Indstillingerne ligger i services/snak (settings/save_settings)."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services import snak, snak_seed

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FLAGS = ("kraever_titel", "har_loesning", "kun_staff", "arkiveret")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON = re.compile(r"^[a-z0-9-]{1,24}$")


def _clean(data: dict, *, new: bool) -> dict:
    out: dict = {}
    if "navn" in data or new:
        navn = (data.get("navn") or "").strip()
        if not 2 <= len(navn) <= 60:
            raise snak.SnakError(400, "Navnet skal være på 2–60 tegn")
        out["navn"] = navn
    if "slug" in data or new:
        slug = (data.get("slug") or "").strip().lower()
        if len(slug) > 40 or not SLUG.match(slug):
            raise snak.SnakError(400, "Adressen må kun have små bogstaver, tal og bindestreger")
        out["slug"] = slug
    if "beskrivelse" in data:
        out["beskrivelse"] = (data.get("beskrivelse") or "").strip()[:300]
    if "farve" in data:
        if not COLOR.match(data.get("farve") or ""):
            raise snak.SnakError(400, "Ugyldig farve")
        out["farve"] = data["farve"]
    if "ikon" in data:
        if not ICON.match(data.get("ikon") or ""):
            raise snak.SnakError(400, "Ugyldigt ikon")
        out["ikon"] = data["ikon"]
    if "sort" in data:
        out["sort"] = max(0, min(int(data["sort"]), 100_000))
    for f in FLAGS:
        if f in data:
            out[f] = bool(data[f])
    return out


async def create_category(session: AsyncSession, data: dict) -> int:
    v = _clean(data, new=True)
    if (await session.execute(text("SELECT 1 FROM sn_categories WHERE slug = :s"), {"s": v["slug"]})).first():
        raise snak.SnakError(409, "Der findes allerede en kategori med den adresse")
    v.setdefault("sort", (await session.execute(text("SELECT coalesce(max(sort), 0) + 10 FROM sn_categories"))).scalar())
    cols = ", ".join(v)
    r = await session.execute(text(f"INSERT INTO sn_categories ({cols}) VALUES ({', '.join(':' + k for k in v)}) RETURNING id"), v)
    cid = r.scalar()
    await session.commit()
    return cid


async def update_category(session: AsyncSession, cid: int, data: dict) -> None:
    v = _clean(data, new=False)
    if not (await session.execute(text("SELECT 1 FROM sn_categories WHERE id = :i"), {"i": cid})).first():
        raise snak.SnakError(404, "Kategorien findes ikke")
    if "slug" in v and (await session.execute(text("SELECT 1 FROM sn_categories WHERE slug = :s AND id <> :i"), {"s": v["slug"], "i": cid})).first():
        raise snak.SnakError(409, "Der findes allerede en kategori med den adresse")
    if v:
        await session.execute(text(f"UPDATE sn_categories SET {', '.join(f'{k} = :{k}' for k in v)} WHERE id = :_id"), {**v, "_id": cid})
    await session.commit()


async def move_posts(session: AsyncSession, cid: int, to_id: int) -> int:
    """Flyt alle opslag fra én kategori til en anden (en kategori med opslag kan kun arkiveres, ikke slettes)."""
    if cid == to_id:
        raise snak.SnakError(400, "Vælg en anden kategori")
    ok = (await session.execute(text("SELECT count(*) FROM sn_categories WHERE id IN (:a, :b) AND NOT (id = :b AND arkiveret)"), {"a": cid, "b": to_id})).scalar()
    if ok != 2:
        raise snak.SnakError(400, "Kategorien findes ikke eller er arkiveret")
    n = (await session.execute(text("UPDATE sn_posts SET category_id = :b WHERE category_id = :a"), {"a": cid, "b": to_id})).rowcount
    await session.commit()
    return n


async def delete_category(session: AsyncSession, cid: int) -> None:
    if (await session.execute(text("SELECT 1 FROM sn_posts WHERE category_id = :i LIMIT 1"), {"i": cid})).first():
        raise snak.SnakError(409, "Kategorien har opslag – arkivér den, eller flyt opslagene først")
    await session.execute(text("DELETE FROM sn_categories WHERE id = :i"), {"i": cid})
    await session.commit()


async def examples(session: AsyncSession) -> dict:
    rows = (await session.execute(text("SELECT id, coalesce(titel, left(body_text, 60)) AS titel, eksempel_navn AS navn, n_comments FROM sn_posts WHERE eksempel ORDER BY id"))).mappings().all()
    return {"rows": [dict(r) for r in rows]}


async def restore_examples(session: AsyncSession) -> int:
    return await snak_seed.seed_examples(session)


async def delete_examples(session: AsyncSession) -> int:
    return await snak_seed.delete_examples(session)


async def stats(session: AsyncSession, weeks: int = 12) -> dict:
    """Opslag og kommentarer pr. uge, aktive brugere og fordeling på kategorier."""
    wk = (await session.execute(text("""
        WITH w AS (SELECT generate_series(date_trunc('week', now()) - make_interval(weeks => :n - 1), date_trunc('week', now()), interval '1 week') AS uge)
        SELECT w.uge::date AS uge,
               (SELECT count(*) FROM sn_posts p WHERE p.status <> 'slettet' AND p.created_at >= w.uge AND p.created_at < w.uge + interval '1 week') AS opslag,
               (SELECT count(*) FROM sn_comments c WHERE c.status <> 'slettet' AND c.created_at >= w.uge AND c.created_at < w.uge + interval '1 week') AS kommentarer
        FROM w ORDER BY w.uge"""), {"n": weeks})).mappings().all()
    active = (await session.execute(text("""
        SELECT count(DISTINCT u) FROM (SELECT user_id AS u FROM sn_posts WHERE created_at > now() - interval '30 days' AND user_id IS NOT NULL
                                       UNION ALL SELECT user_id FROM sn_comments WHERE created_at > now() - interval '30 days' AND user_id IS NOT NULL
                                       UNION ALL SELECT user_id FROM sn_reactions WHERE created_at > now() - interval '30 days') t"""))).scalar() or 0
    cats = (await session.execute(text("""SELECT c.navn, c.farve, count(p.id) AS antal FROM sn_categories c LEFT JOIN sn_posts p ON p.category_id = c.id AND p.status = 'synlig'
                                          GROUP BY c.id ORDER BY c.sort, c.id"""))).mappings().all()
    totals = (await session.execute(text("""SELECT (SELECT count(*) FROM sn_posts WHERE status <> 'slettet') AS opslag, (SELECT count(*) FROM sn_comments WHERE status <> 'slettet') AS kommentarer,
                                                   (SELECT count(*) FROM sn_reactions) AS reaktioner, (SELECT count(*) FROM sn_reports WHERE handled_at IS NULL) AS anmeldelser"""))).mappings().first()
    return {"uger": [{"uge": r["uge"].isoformat(), "opslag": r["opslag"], "kommentarer": r["kommentarer"]} for r in wk], "aktive_30d": active,
            "kategorier": [dict(r) for r in cats], "total": dict(totals)}
