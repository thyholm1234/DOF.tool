"""Fotomodulet, læsesiden: galleri med søgning, fotosiden, likes, visninger og adgang til medier (docs/netfugl-afloesning-spec.md §9.1–9.2, §4.3).

Ingen SQL her må blande ``gal_*`` med observationstabeller; koblingen til DOFbasen sker via ``gal_photos.obsid`` og separate forespørgsler."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, version
from portal.services import photo_codes
from portal.timeutil import dk_today

PER_PAGES = (24, 48, 96)
SORTS = {"dato": "p.dato DESC NULLS LAST, p.id DESC", "nyeste": "coalesce(p.published_at, p.uploaded_at) DESC, p.id DESC",
         "likes": "(p.likes + p.likes_imported) DESC, p.id DESC", "kommenteret": "(SELECT max(k.created_at) FROM gal_comments k WHERE k.photo_id = p.id AND k.status = 'synlig' AND NOT k.intern) DESC NULLS LAST, p.id DESC",
         "set": "(p.views + p.views_imported) DESC, p.id DESC"}
ORIGIN_STATUS = ("afventer", "publiceret", "afvist", "afpubliceret")
COUNT_TTL, PAGE_TTL = 120, 60


def _iso(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _url(key: str | None) -> str | None:
    return "/media/art/" + key if key else None


def _ints(v) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", str(v or "")) if x.strip().isdigit()]


def _codes(v, n=16) -> list[str]:
    return [x for x in re.split(r"[,\s]+", str(v or "")) if x and re.fullmatch(r"[a-z0-9_]{1,%d}" % n, x)]


# ---------------------------------------------------------------- søgning

def build_where(f: dict) -> tuple[str, dict]:
    """WHERE-del og parametre til galleriet ud fra et (normaliseret) filter. Gruppens øvrige fotos skjules bag coveret."""
    w, p = ["p.status = ANY(:status)"], {"status": f.get("status") or ["publiceret"]}
    if q := (f.get("q") or "").strip():
        p["q"] = q
        p["qlike"] = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
        w.append("""(to_tsvector('danish', coalesce(p.titel, '') || ' ' || coalesce(p.lokalitet, '') || ' ' || coalesce(p.beskrivelse_md, '')) @@ websearch_to_tsquery('danish', :q)
                   OR ph.navn ILIKE :qlike OR p.titel ILIKE :qlike OR p.lokalitet ILIKE :qlike
                   OR EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND (b.navn ILIKE :qlike OR b.latin ILIKE :qlike)))""")
    for key, op in (("fra", ">="), ("til", "<=")):
        try:
            p[key] = date.fromisoformat(str(f.get(key) or "")[:10])
        except ValueError:
            continue
        w.append(f"p.dato {op} :{key}")
    if months := [m for m in _ints(f.get("md")) if 1 <= m <= 12]:
        w.append("extract(month FROM p.dato)::int = ANY(:md)")
        p["md"] = months
    if arts := _ints(f.get("art")):
        w.append("EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND (b.artnr = ANY(:art) OR b.artnr IN (SELECT s.artnr FROM species s WHERE s.list_artnr = ANY(:art))))")
        p["art"] = arts
    if taxa := _ints(f.get("taxon")):
        w.append("EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.taxon_id = ANY(:taxon))")
        p["taxon"] = taxa
    if f.get("fotograf"):
        w.append("p.photographer_id = :fot")
        p["fot"] = int(f["fotograf"])
    if f.get("land"):
        w.append("p.land_id = ANY(:land)")
        p["land"] = _ints(f["land"])
    if f.get("lok"):
        w.append("p.lokalitet ILIKE :lok")
        p["lok"] = "%" + str(f["lok"]).replace("%", r"\%").replace("_", r"\_") + "%"
    if f.get("loknr"):
        w.append("p.loknr = :loknr")
        p["loknr"] = int(f["loknr"])
    for key, col in (("koen", "koen"), ("alder", "alder"), ("dragt", "dragt"), ("adf", "adfaerd")):
        if vals := _codes(f.get(key)):
            w.append(f"EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.{col} = ANY(:{key}))")
            p[key] = vals
    if tags := _codes(f.get("tag"), 24):
        w.append("p.tags @> :tags")
        p["tags"] = tags
    if f.get("sj") and f.get("_sj_sql"):
        w.append(f["_sj_sql"])
        p["sj"] = _codes(f["sj"], 24)
    if f.get("owner"):
        w.append("p.user_id = :owner")
        p["owner"] = int(f["owner"])
    base = " AND ".join(w)
    # Et gruppekort: coveret, hvis det matcher; ellers det matchende medlem med laveste id (så en søgning også finder grupperede fotos)
    inner = re.sub(r"\bph\.", "ph2.", re.sub(r"\bp\.", "p2.", base))
    group = ("(p.group_id IS NULL OR NOT EXISTS (SELECT 1 FROM gal_photos p2 LEFT JOIN gal_photographers ph2 ON ph2.id = p2.photographer_id "
             f"WHERE p2.group_id = p.group_id AND p2.id <> p.id AND {inner} AND (p2.id = g.cover_photo_id OR (p.id IS DISTINCT FROM g.cover_photo_id AND p2.id < p.id))))")
    return base + " AND " + group, p


FROM = """FROM gal_photos p LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id LEFT JOIN gal_groups g ON g.id = p.group_id
          LEFT JOIN gal_photos cp ON cp.id = g.cover_photo_id LEFT JOIN content_media m ON m.id = p.media_id"""


def card(r) -> dict:
    return {"id": r["id"], "titel": r["titel"], "thumb": _url(r["thumb_key"] or r["storage_key"]),
            "medium": _url(r["medium_key"] or r["storage_key"]), "w": r["width"], "h": r["height"],
            "arter": [{"navn": b["navn"], "artnr": b["artnr"]} for b in (r["birds"] or [])], "dato": _iso(r["dato"]), "lokalitet": r["lokalitet"], "land": r["land"],
            "fotograf": {"id": r["photographer_id"], "navn": r["fotograf"]}, "likes": r["likes"] + r["likes_imported"], "views": r["views"] + r["views_imported"],
            "kommentarer": r["comments"], "gruppe": max(0, (r["group_n"] or 1) - 1), "status": r["status"]}


async def _sj_sql(session: AsyncSession) -> str | None:
    """Sjældenhed via DK-listen (krydslistemodulet), hvis tabellerne findes."""
    if (await session.execute(text("SELECT to_regclass('kl_species_list_entries')"))).scalar() is None:
        return None
    return ("EXISTS (SELECT 1 FROM gal_birds b JOIN kl_taxa t ON t.id = b.taxon_id JOIN kl_species_list_entries e ON e.taxon_id = t.id "
            "JOIN kl_species_lists l ON l.id = e.list_id AND l.key = 'dk' WHERE b.photo_id = p.id AND b.pos = 0 AND e.sjaeldenhed = ANY(:sj))")


def normalise(q: dict) -> dict:
    sort = q.get("sort") if q.get("sort") in SORTS else "dato"
    per = int(q.get("per") or 24)
    return {**{k: (str(v).strip() if v is not None else "") for k, v in q.items() if k not in ("per", "side", "sort", "status", "owner")},
            "sort": sort, "per": per if per in PER_PAGES else 24, "side": max(1, int(q.get("side") or 1))}


async def search(session: AsyncSession, q: dict, *, status: list[str] | None = None, owner: int | None = None, cache: bool = True) -> dict:
    f = normalise(q)
    f["status"] = status or ["publiceret"]
    f["owner"] = owner
    ver = await version("photos")
    key = "photos:list:" + str(ver) + ":" + json.dumps(sorted((k, v) for k, v in f.items() if v not in ("", None)), default=str)
    if cache and (hit := await kv().get(key)):
        return json.loads(hit)
    f["_sj_sql"] = await _sj_sql(session)
    where, params = build_where(f)
    ckey = "photos:count:" + str(ver) + ":" + json.dumps(sorted((k, v) for k, v in f.items() if v not in ("", None) and k not in ("side", "sort", "per")), default=str)
    total = int(c) if cache and (c := await kv().get(ckey)) else None
    if total is None:
        total = (await session.execute(text(f"SELECT count(*) {FROM} WHERE {where}"), params)).scalar() or 0
        await kv().set(ckey, str(total), ex=COUNT_TTL)
    rows = (await session.execute(text(f"""
        SELECT p.id, p.titel, p.width, p.height, p.dato, p.lokalitet, p.status, p.photographer_id, ph.navn AS fotograf, p.likes, p.likes_imported, p.views,
               p.views_imported, p.comments, m.thumb_key, m.medium_key, m.storage_key, (SELECT c.navn FROM gal_countries c WHERE c.id = p.land_id) AS land,
               (SELECT count(*) FROM gal_photos x WHERE x.group_id = p.group_id AND x.status = 'publiceret' AND p.group_id IS NOT NULL) AS group_n,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('navn', b.navn, 'artnr', b.artnr) ORDER BY b.pos), '[]'::jsonb) FROM gal_birds b WHERE b.photo_id = p.id) AS birds
        {FROM} WHERE {where} ORDER BY {SORTS[f['sort']]} LIMIT :lim OFFSET :off"""), {**params, "lim": f["per"], "off": (f["side"] - 1) * f["per"]})).mappings().all()
    out = {"total": total, "page": f["side"], "per": f["per"], "rows": [card(r) for r in rows]}
    if cache:
        await kv().set(key, json.dumps(out, ensure_ascii=False, default=str), ex=PAGE_TTL)
    return out


async def suggestions(session: AsyncSession) -> dict:
    """Forslag til galleriets filtre: de arter/racer og lokaliteter, der faktisk findes på publicerede fotos (med antal fotos)."""
    key = "photos:suggest:" + str(await version("photos"))
    if hit := await kv().get(key):
        return json.loads(hit)
    arter = (await session.execute(text("""
        SELECT b.artnr, coalesce(max(s.navn), max(b.navn)) AS navn, coalesce(max(s.latin), max(b.latin)) AS latin, count(DISTINCT b.photo_id) AS n
        FROM gal_birds b JOIN gal_photos p ON p.id = b.photo_id AND p.status = 'publiceret' LEFT JOIN species s ON s.artnr = b.artnr
        WHERE b.artnr IS NOT NULL GROUP BY b.artnr ORDER BY lower(coalesce(max(s.navn), max(b.navn)))"""))).mappings().all()
    lok = (await session.execute(text("""
        SELECT min(lokalitet) AS navn, count(*) AS n FROM gal_photos WHERE status = 'publiceret' AND coalesce(trim(lokalitet), '') <> ''
        GROUP BY lower(trim(lokalitet)) ORDER BY lower(trim(lokalitet))"""))).mappings().all()
    out = {"arter": [{"artnr": r["artnr"], "navn": r["navn"], "latin": r["latin"], "n": r["n"]} for r in arter],
           "lokaliteter": [{"navn": r["navn"], "n": r["n"]} for r in lok]}
    await kv().set(key, json.dumps(out, ensure_ascii=False), ex=300)
    return out


async def top_for_species(session: AsyncSession, artnr: int, n: int = 10) -> dict:
    """Artens mest likede fotos (til artssidens galleri): galleriets søgning på arten sorteret efter likes, de første ``n``."""
    d = await search(session, {"art": str(artnr), "sort": "likes", "per": PER_PAGES[0]})
    return {"total": d["total"], "rows": d["rows"][:max(1, min(n, PER_PAGES[0]))]}


# ---------------------------------------------------------------- fotosiden

PHOTO = """
    SELECT p.*, m.storage_key, m.medium_key, m.thumb_key, m.mime, ph.navn AS fotograf_navn, ph.user_id AS fotograf_user, ph.obserkode AS fotograf_obserkode,
           ph.standard_rettighed, ph.dof_samtykke, (SELECT c.navn FROM gal_countries c WHERE c.id = p.land_id) AS land_navn,
           (SELECT l.navn FROM locations l WHERE l.loknr = p.loknr) AS lok_navn, u.navn AS ejer_navn, lb.navn AS laast_af
    FROM gal_photos p LEFT JOIN content_media m ON m.id = p.media_id LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
         LEFT JOIN users u ON u.id = p.user_id LEFT JOIN users lb ON lb.id = p.locked_by
    WHERE p.id = :id"""


async def can_see(r, user_id: int | None, moderator: bool) -> bool:
    return r["status"] == "publiceret" or moderator or (user_id is not None and r["user_id"] == user_id)


async def get(session: AsyncSession, pid: int, *, user_id: int, moderator: bool, hide_obs: bool = False) -> dict | None:
    r = (await session.execute(text(PHOTO), {"id": pid})).mappings().first()
    if r is None or not await can_see(r, user_id, moderator):
        return None
    birds = (await session.execute(text("""
        SELECT b.pos, b.artnr, b.taxon_id, b.navn, b.latin, b.adfaerd, b.koen, b.alder, b.dragt, s.navn AS dof_navn, s.klass FROM gal_birds b
        LEFT JOIN species s ON s.artnr = b.artnr WHERE b.photo_id = :id ORDER BY b.pos"""), {"id": pid})).mappings().all()
    codes = {(c["kind"], c["code"]): c["text"] for c in (await session.execute(text("SELECT kind, code, text FROM gal_codes"))).mappings()}
    siblings = []
    if r["group_id"]:
        siblings = [{"id": x["id"], "thumb": _url(x["thumb_key"] or x["storage_key"])} for x in (await session.execute(text("""
            SELECT p.id, m.thumb_key, m.storage_key FROM gal_photos p LEFT JOIN content_media m ON m.id = p.media_id
            WHERE p.group_id = :g AND (p.status = 'publiceret' OR :mod OR p.user_id = :u) ORDER BY p.dato, p.id"""), {"g": r["group_id"], "mod": moderator, "u": user_id})).mappings()]
    rett = r["rettighed"] or r["standard_rettighed"] or 2
    liked = (await session.execute(text("SELECT 1 FROM gal_likes WHERE photo_id = :p AND user_id = :u"), {"p": pid, "u": user_id})).first() is not None
    likers = [x[0] for x in (await session.execute(text("SELECT u.navn FROM gal_likes l JOIN users u ON u.id = l.user_id WHERE l.photo_id = :p ORDER BY l.created_at LIMIT 20"), {"p": pid})).all()]
    out = {
        "id": r["id"], "netfugl_id": r["netfugl_id"], "status": r["status"], "afvist_grund": r["afvist_grund"] if (moderator or r["user_id"] == user_id) else None,
        "titel": r["titel"], "beskrivelse_html": r["beskrivelse_html"], "beskrivelse_md": r["beskrivelse_md"], "dato": _iso(r["dato"]),
        "url": _url(r["storage_key"]), "medium": _url(r["medium_key"] or r["storage_key"]), "thumb": _url(r["thumb_key"] or r["storage_key"]), "w": r["width"], "h": r["height"],
        "land": {"id": r["land_id"], "navn": r["land_navn"]} if r["land_id"] else None, "lokalitet": r["lokalitet"], "loknr": r["loknr"], "lok_navn": r["lok_navn"],
        "lat": r["lat"], "lon": r["lon"], "tags": r["tags"] or [], "kamera": r["kamera"] or {},
        "rettighed": {"id": rett, "tekst": photo_codes.rettigheder().get(rett), "egen": r["rettighed"] is not None},
        "fotograf": {"id": r["photographer_id"], "navn": r["fotograf_navn"], "user_id": r["fotograf_user"], "obserkode": r["fotograf_obserkode"]},
        "fugle": [{"pos": b["pos"], "artnr": b["artnr"], "navn": b["dof_navn"] or b["navn"], "netfugl_navn": b["navn"], "latin": b["latin"],
                   "adfaerd": codes.get(("adfaerd", b["adfaerd"])), "koen": codes.get(("koen", b["koen"])), "alder": codes.get(("alder", b["alder"])),
                   "dragt": codes.get(("dragt", b["dragt"])), "klass": b["klass"], "kode": {k: b[k] for k in ("adfaerd", "koen", "alder", "dragt")}} for b in birds],
        "likes": r["likes"] + r["likes_imported"], "views": r["views"] + r["views_imported"], "kommentarer": r["comments"], "liked": liked, "likers": likers,
        "uploadet": _iso(r["uploaded_at"]), "publiceret": _iso(r["published_at"]), "kilde": r["source"], "gruppe": siblings,
        "laast": {"af": r["laast_af"], "at": _iso(r["locked_at"])} if r["locked_by"] else None,
        "obsid": None if hide_obs else r["obsid"], "ejer": r["user_id"], "egen": r["user_id"] is not None and r["user_id"] == user_id,
    }
    return out


async def by_netfugl(session: AsyncSession, nid: int) -> int | None:
    return (await session.execute(text("SELECT id FROM gal_photos WHERE netfugl_id = :n"), {"n": nid})).scalar()


async def register_view(session: AsyncSession, photo_id: int, user_id: int) -> None:
    """Én visning pr. bruger pr. døgn (Valkey-sæt)."""
    key = f"photo:viewed:{photo_id}:{dk_today().isoformat()}"
    if await kv().sadd(key, user_id):
        await kv().expire(key, 172800)
        await session.execute(text("UPDATE gal_photos SET views = views + 1 WHERE id = :p"), {"p": photo_id})
        await session.commit()


async def toggle_like(session: AsyncSession, photo_id: int, user_id: int) -> dict:
    row = (await session.execute(text("SELECT status, user_id FROM gal_photos WHERE id = :p"), {"p": photo_id})).first()
    if row is None or row[0] != "publiceret":
        raise LookupError("Fotoet findes ikke")
    gone = (await session.execute(text("DELETE FROM gal_likes WHERE photo_id = :p AND user_id = :u RETURNING 1"), {"p": photo_id, "u": user_id})).first()
    if not gone:
        await session.execute(text("INSERT INTO gal_likes (user_id, photo_id) VALUES (:u, :p) ON CONFLICT DO NOTHING"), {"p": photo_id, "u": user_id})
    n = (await session.execute(text("UPDATE gal_photos SET likes = (SELECT count(*) FROM gal_likes WHERE photo_id = :p) WHERE id = :p RETURNING likes, likes_imported"),
                               {"p": photo_id})).first()
    await session.commit()
    await kv().incr("ver:photos")
    return {"liked": not gone, "likes": n[0] + n[1]}


# ---------------------------------------------------------------- medier

async def media_info(session: AsyncSession, key: str) -> dict:
    """Hører mediet til et foto? Cachet 60 s pr. fil, fordi galleriet henter mange miniaturer."""
    ck = "photo:media:" + key
    if hit := await kv().get(ck):
        return json.loads(hit)
    r = (await session.execute(text("""
        SELECT m.owner_kind, p.id, p.status, p.user_id FROM content_media m LEFT JOIN gal_photos p ON p.media_id = m.id
        WHERE m.storage_key = :k OR m.thumb_key = :k OR m.medium_key = :k ORDER BY (p.id IS NULL) LIMIT 1"""), {"k": key})).first()
    info = {"photo": bool(r and r[0] == "foto"), "id": r[1] if r else None, "status": r[2] if r else None, "user_id": r[3] if r else None}
    if r and r[0] == "banner":
        info["banner"] = True
    await kv().set(ck, json.dumps(info), ex=60)
    return info
