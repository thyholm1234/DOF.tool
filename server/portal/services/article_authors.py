"""Artikler: forfatterregister (art_authors) med fletning, opdeling og CSV (docs/netfugl-afloesning-spec.md §12).

Artiklernes forfattere læses fra registret. ``art_articles.authors`` (JSON) vedligeholdes i denne udgave kun som kopi til tilbagerulning
og læses ikke længere; kolonnen fjernes i en senere migration."""

from __future__ import annotations

import csv
import io
import re
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import ArtArticle, ArtArticleAuthor, ArtAuthor

COLUMNS = ["id", "navn", "skjult", "artikler", "netfugl_id", "bruger", "bio"]
SPLIT = re.compile(r"\s+og\s+|\s*,\s*|\s*&\s*|\s*/\s*", re.I)


class Invalid(Exception):
    pass


def norm(navn: str | None) -> str:
    s = unicodedata.normalize("NFKC", navn or "").casefold()
    s = re.sub(r"[.,;:'’`´\-–—_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


async def find_or_create(session: AsyncSession, navn: str, *, netfugl_id: int | None = None, user_id: int | None = None) -> ArtAuthor:
    """Forfatteren med dette Netfugl-id, ellers med samme normaliserede navn; ellers oprettes en ny. Flettede/skjulte forfattere genbruges."""
    navn = " ".join((navn or "").split())[:160]
    if not navn:
        raise Invalid("Forfatterens navn mangler")
    a = None
    if netfugl_id is not None:
        a = (await session.execute(select(ArtAuthor).where(ArtAuthor.netfugl_forfatter_id == netfugl_id))).scalar_one_or_none()
    if a is None:
        a = (await session.execute(select(ArtAuthor).where(ArtAuthor.navn_norm == norm(navn)).order_by(ArtAuthor.id).limit(1))).scalar_one_or_none()
        if a is not None and netfugl_id is not None and a.netfugl_forfatter_id is None:
            a.netfugl_forfatter_id = netfugl_id
    if a is None:
        a = ArtAuthor(navn=navn, navn_norm=norm(navn), netfugl_forfatter_id=netfugl_id, user_id=user_id)
        session.add(a)
        await session.flush()
    return a


async def set_for_article(session: AsyncSession, article_id: int, authors: list[dict]) -> list[dict]:
    """Skriv artiklens forfattere (rækkefølgen bevares). authors: [{navn, id?, user_id?, netfugl_id?}]. Returnerer kopien til den gamle JSON-kolonne."""
    await session.execute(text("DELETE FROM art_article_authors WHERE article_id = :a"), {"a": article_id})
    out, seen = [], set()
    for i, x in enumerate(authors or []):
        navn = (x.get("navn") or "").strip()
        a = await session.get(ArtAuthor, x["id"]) if x.get("id") else None
        if a is None:
            if not navn:
                continue
            a = await find_or_create(session, navn, netfugl_id=x.get("netfugl_id") or x.get("netfugl_forfatter_id"), user_id=x.get("user_id"))
        if a.id in seen:
            continue
        seen.add(a.id)
        session.add(ArtArticleAuthor(article_id=article_id, author_id=a.id, sort=i))
        out.append({"navn": a.navn, **({"netfugl_id": a.netfugl_forfatter_id} if a.netfugl_forfatter_id else {}), **({"user_id": a.user_id} if a.user_id else {})})
    await session.flush()
    return out


async def backfill(session: AsyncSession) -> int:
    """Migration: opret forfattere og koblinger ud fra artiklernes JSON. Idempotent (artikler med koblinger røres ikke). Splitter ikke automatisk."""
    rows = (await session.execute(text("""
        SELECT a.id, a.authors FROM art_articles a WHERE jsonb_array_length(a.authors) > 0
          AND NOT EXISTS (SELECT 1 FROM art_article_authors x WHERE x.article_id = a.id) ORDER BY a.id"""))).all()
    n = 0
    for aid, authors in rows:
        await set_for_article(session, aid, [{"navn": x.get("navn"), "netfugl_id": x.get("netfugl_id"), "user_id": x.get("user_id")} for x in authors if x.get("navn")])
        n += 1
        if n % 500 == 0:
            await session.commit()
    await session.commit()
    await bump("articles")
    return n


async def _sync_json(session: AsyncSession, article_ids: list[int]) -> None:
    """Hold den gamle JSON-kolonne i takt med registret (kopi til tilbagerulning)."""
    if not article_ids:
        return
    await session.execute(text("""
        UPDATE art_articles a SET authors = coalesce((SELECT jsonb_agg(jsonb_build_object('navn', au.navn) ||
            CASE WHEN au.netfugl_forfatter_id IS NOT NULL THEN jsonb_build_object('netfugl_id', au.netfugl_forfatter_id) ELSE '{}'::jsonb END
            ORDER BY x.sort) FROM art_article_authors x JOIN art_authors au ON au.id = x.author_id WHERE x.article_id = a.id), '[]'::jsonb)
        WHERE a.id = ANY(:ids)"""), {"ids": article_ids})


# ---------------------------------------------------------------- læsning

async def search(session: AsyncSession, q: str, limit: int = 15) -> list[dict]:
    """Forfattersøgning til editoren."""
    q = q.strip()
    rows = (await session.execute(text("""
        SELECT au.id, au.navn, au.user_id, (SELECT count(*) FROM art_article_authors x WHERE x.author_id = au.id) AS n FROM art_authors au
        WHERE (:q = '' OR au.navn ILIKE :like) ORDER BY (au.navn ILIKE :p) DESC, n DESC, au.navn LIMIT :n"""), {"q": q, "like": f"%{q}%", "p": f"{q}%", "n": limit})).mappings().all()
    return [dict(r) for r in rows]


async def admin_list(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT au.id, au.navn, au.skjult, au.netfugl_forfatter_id AS netfugl_id, au.user_id, au.bio_md, u.obserkode, u.navn AS bruger,
               (SELECT count(*) FROM art_article_authors x WHERE x.author_id = au.id) AS n,
               (SELECT coalesce(jsonb_agg(t.title), '[]'::jsonb) FROM (SELECT a.title FROM art_article_authors x JOIN art_articles a ON a.id = x.article_id
                  WHERE x.author_id = au.id ORDER BY a.dato DESC LIMIT 3) t) AS eksempler
        FROM art_authors au LEFT JOIN users u ON u.id = au.user_id ORDER BY au.navn_norm, au.id"""))).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- rettelser

async def _merge_into(session: AsyncSession, src: ArtAuthor, dst: ArtAuthor) -> int:
    """Flyt src's artikler til dst (dst's placering bevares ved dubletter), og slet src. Netfugl-id, bruger og bio overtages, hvis dst mangler dem."""
    moved = (await session.execute(text("""
        INSERT INTO art_article_authors (article_id, author_id, sort) SELECT article_id, :d, sort FROM art_article_authors WHERE author_id = :s
        ON CONFLICT (article_id, author_id) DO NOTHING RETURNING article_id"""), {"s": src.id, "d": dst.id})).all()
    ids = [r[0] for r in (await session.execute(text("SELECT article_id FROM art_article_authors WHERE author_id IN (:s, :d)"), {"s": src.id, "d": dst.id})).all()]
    nid, uid, bio = src.netfugl_forfatter_id, src.user_id, src.bio_md
    await session.delete(src)
    await session.flush()
    if dst.netfugl_forfatter_id is None and nid is not None:
        dst.netfugl_forfatter_id = nid
    dst.user_id = dst.user_id or uid
    dst.bio_md = dst.bio_md or bio
    await _sync_json(session, ids)
    return len(moved)


async def save_many(session: AsyncSession, changes: list[dict]) -> dict:
    """Gem rettelser ({id, navn?, skjult?, bio_md?, user_id?}) i én transaktion. Omdøbes en forfatter til et navn, en anden har, flettes de to
    (artiklerne flyttes; rækkens skjult/bio/bruger overskriver). Alt eller intet."""
    if len(changes) > 5000:
        raise Invalid("Højst 5000 ændringer ad gangen")
    by_id = {int(c["id"]): c for c in changes}
    auth = {a.id: a for a in (await session.execute(select(ArtAuthor).where(ArtAuthor.id.in_(by_id.keys() or {0})))).scalars()}
    if missing := by_id.keys() - auth.keys():
        raise Invalid(f"{len(missing)} forfattere findes ikke længere (fx id {min(missing)}) – genindlæs siden")
    renamed = {i: c["navn"].strip() for i, c in by_id.items() if c.get("navn") and norm(c["navn"]) != auth[i].navn_norm}
    flettet, touched = [], set()
    # navne efter omdøbning inden for omgangen (for at opdage navnebytte)
    new_norm = {i: norm(n) for i, n in renamed.items()}
    for i, navn in renamed.items():
        if not norm(navn):
            raise Invalid("Navnet må ikke være tomt")
        hit = (await session.execute(select(ArtAuthor).where(ArtAuthor.navn_norm == norm(navn), ArtAuthor.id != i).order_by(ArtAuthor.id).limit(1))).scalar_one_or_none()
        if hit is not None and hit.id not in renamed:
            src, dst = auth[i], hit
            c = by_id[i]
            if "skjult" in c:
                dst.skjult = bool(c["skjult"])
            if "bio_md" in c:
                dst.bio_md = c["bio_md"] or None
            if "user_id" in c:
                dst.user_id = c["user_id"]
            moved = await _merge_into(session, src, dst)
            flettet.append({"id": i, "into": dst.id, "flyttet": moved})
            by_id.pop(i)
            touched.add(dst.id)
    for n_a, n_b in ((a, b) for a in new_norm for b in new_norm if a < b):
        if new_norm[n_a] == new_norm[n_b]:
            raise Invalid(f"To forfattere kan ikke begge hedde «{renamed[n_a]}» – brug «Flet»")
    for i, c in by_id.items():
        a = auth[i]
        if i in renamed:
            a.navn, a.navn_norm = renamed[i], norm(renamed[i])
        if "skjult" in c:
            a.skjult = bool(c["skjult"])
        if "bio_md" in c:
            a.bio_md = (c["bio_md"] or "").strip() or None
        if "user_id" in c:
            a.user_id = c["user_id"] or None
        touched.add(i)
    await session.flush()
    ids = [r[0] for r in (await session.execute(text("SELECT DISTINCT article_id FROM art_article_authors WHERE author_id = ANY(:a)"), {"a": list(touched) or [0]})).all()]
    await _sync_json(session, ids)
    await session.commit()
    await bump("articles")
    return {"gemt": len(changes), "flettet": flettet}


async def merge(session: AsyncSession, src_id: int, dst_id: int) -> int:
    if src_id == dst_id:
        raise Invalid("Vælg to forskellige forfattere")
    src, dst = await session.get(ArtAuthor, src_id), await session.get(ArtAuthor, dst_id)
    if src is None or dst is None:
        raise Invalid("Forfatteren findes ikke")
    n = await _merge_into(session, src, dst)
    await session.commit()
    await bump("articles")
    return n


async def split(session: AsyncSession, author_id: int, names: list[str]) -> list[int]:
    """Del «Lars Witting og Sakari Kauppinen» op i flere forfattere: i hver artikel erstattes forfatteren af de nye på samme plads. names tomt = foreslå automatisk."""
    a = await session.get(ArtAuthor, author_id)
    if a is None:
        raise Invalid("Forfatteren findes ikke")
    parts = [p.strip() for p in (names or SPLIT.split(a.navn)) if p and p.strip()]
    if len(parts) < 2:
        raise Invalid("Angiv mindst to navne")
    new = [await find_or_create(session, p) for p in parts]
    rows = (await session.execute(text("SELECT article_id, sort FROM art_article_authors WHERE author_id = :a"), {"a": author_id})).all()
    for aid, srt in rows:
        await session.execute(text("DELETE FROM art_article_authors WHERE article_id = :a AND author_id = :o"), {"a": aid, "o": author_id})
        for k, au in enumerate(new):
            await session.execute(text("INSERT INTO art_article_authors (article_id, author_id, sort) VALUES (:a, :u, :s) ON CONFLICT DO NOTHING"), {"a": aid, "u": au.id, "s": srt * 10 + k})
    ids = [r[0] for r in rows]
    nid = a.netfugl_forfatter_id
    await session.delete(a)
    await session.flush()
    if nid is not None:  # arkivets id følger med det første navn, så en senere import ikke genskaber den sammensatte forfatter
        new[0].netfugl_forfatter_id = new[0].netfugl_forfatter_id or nid
    await _sync_json(session, ids)
    await session.commit()
    await bump("articles")
    return [x.id for x in new]


# ---------------------------------------------------------------- CSV

def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([r["id"], r["navn"], "ja" if r["skjult"] else "", r["n"], r["netfugl_id"] or "", r["obserkode"] or "", (r["bio_md"] or "").replace("\n", " ")])
    return "﻿" + buf.getvalue()


async def parse_import(session: AsyncSession, raw: bytes) -> dict:
    """Læs en rettet eksport; returnér kun de rækker, der afviger (id kendes), som ugemte rettelser. Intet gemmes."""
    txt = raw.decode("utf-8-sig", "replace")
    first = txt.splitlines()[0] if txt.strip() else ""
    delim = max(";\t,", key=first.count)
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(txt), delimiter=delim)]
    if not rows or "id" not in rows[0]:
        raise Invalid("CSV-filen skal have en kolonne «id» (brug eksporten som skabelon)")
    cur = {a["id"]: a for a in await admin_list(session)}
    changes, ukendte = [], []
    for r in rows:
        if not r.get("id", "").isdigit() or int(r["id"]) not in cur:
            ukendte.append(r.get("id") or r.get("navn") or "?")
            continue
        c, ch = cur[int(r["id"])], {}
        if r.get("navn") and r["navn"] != c["navn"]:
            ch["navn"] = r["navn"]
        if "skjult" in r and (r["skjult"].lower() in ("ja", "j", "x", "1", "true")) != c["skjult"]:
            ch["skjult"] = r["skjult"].lower() in ("ja", "j", "x", "1", "true")
        if "bio" in r and r["bio"] != (c["bio_md"] or "").replace("\n", " "):
            ch["bio_md"] = r["bio"]
        if ch:
            changes.append({"id": c["id"], **ch})
    return {"changes": changes, "ukendte": ukendte, "rækker": len(rows)}
