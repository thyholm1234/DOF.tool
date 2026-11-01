"""Krydslister: artslister (DK, VP …), World Bird List og artssider (docs/netfugl-afloesning-spec.md §11.4)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached
from portal.services import krydslister
from portal.timeutil import dk_today


async def species_lists(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT l.id, l.key, l.navn, l.beskrivelse, l.sort, (SELECT count(*) FROM kl_species_list_entries e WHERE e.list_id = l.id) AS antal
        FROM kl_species_lists l ORDER BY l.sort, l.id"""))).mappings().all()
    return [dict(r) for r in rows]


async def species_list(session: AsyncSession, key: str, q: str = "") -> dict:
    l = (await session.execute(text("SELECT id, key, navn, beskrivelse FROM kl_species_lists WHERE key = :k"), {"k": key})).mappings().first()
    if l is None:
        raise krydslister.KlError(404, "Artslisten findes ikke")
    where, args = "e.list_id = :l", {"l": l["id"]}
    if q.strip():
        where += " AND (t.dansk ILIKE :q OR t.latin ILIKE :q OR t.engelsk ILIKE :q)"
        args["q"] = f"%{q.strip()}%"
    rows = (await session.execute(text(f"""
        SELECT t.id, t.dansk, t.engelsk, t.latin, t.artnr, e.kategorier, e.sjaeldenhed, e.sort FROM kl_species_list_entries e JOIN kl_taxa t ON t.id = e.taxon_id
        WHERE {where} ORDER BY e.sort, t.id"""), args)).mappings().all()
    return {"liste": dict(l), "rows": [{"id": r["id"], "navn": r["dansk"] or r["engelsk"] or r["latin"], "latin": r["latin"], "engelsk": r["engelsk"], "artnr": r["artnr"],
                                       "kategorier": r["kategorier"] or [], "sjaeldenhed": r["sjaeldenhed"], "su": r["sjaeldenhed"] == "su", "sort": r["sort"]} for r in rows]}


async def search_taxa(session: AsyncSession, q: str, list_key: str | None = None, limit: int = 25) -> list[dict]:
    """Søg i World Bird List (evt. kun arter på en artsliste) til «tilføj kryds» og admin."""
    if len(q.strip()) < 2:
        return []
    join, where = "", ""
    args: dict = {"q": f"%{q.strip()}%", "p": f"{q.strip()}%", "n": limit}
    if list_key:
        join = "JOIN kl_species_list_entries e ON e.taxon_id = t.id JOIN kl_species_lists l ON l.id = e.list_id AND l.key = :lk"
        args["lk"] = list_key
    rows = (await session.execute(text(f"""
        SELECT t.id, t.dansk, t.engelsk, t.latin, t.artnr, t.rang {', e.sjaeldenhed' if list_key else ", NULL AS sjaeldenhed"} FROM kl_taxa t {join}
        WHERE t.aktiv AND (t.dansk ILIKE :q OR t.latin ILIKE :q OR t.engelsk ILIKE :q) {where}
        ORDER BY (coalesce(t.dansk, t.engelsk) ILIKE :p) DESC, length(coalesce(t.dansk, t.engelsk, t.latin)) LIMIT :n"""), args)).mappings().all()
    return [{"id": r["id"], "navn": r["dansk"] or r["engelsk"] or r["latin"], "latin": r["latin"], "engelsk": r["engelsk"], "artnr": r["artnr"], "rang": r["rang"],
             "su": r["sjaeldenhed"] == "su"} for r in rows]


async def world_bird_list(session: AsyncSession, q: str = "") -> dict:
    """Orden → familie → art. Uden familiedata (før AviList er indlæst) vises arterne i én gruppe."""
    where, args = "t.aktiv AND t.rang = 'art'", {}
    if q.strip():
        where += " AND (t.dansk ILIKE :q OR t.latin ILIKE :q OR t.engelsk ILIKE :q OR t.familie ILIKE :q OR t.orden ILIKE :q)"
        args["q"] = f"%{q.strip()}%"
    rows = (await session.execute(text(f"""
        SELECT t.id, t.orden, t.familie, t.dansk, t.engelsk, t.latin, t.artnr FROM kl_taxa t WHERE {where} ORDER BY t.sort, t.id LIMIT 3000"""), args)).mappings().all()
    total = (await session.execute(text("SELECT count(*) FROM kl_taxa WHERE aktiv AND rang = 'art'"))).scalar() or 0
    version = (await session.execute(text("SELECT version, count(*) FROM kl_taxa WHERE aktiv GROUP BY 1 ORDER BY 2 DESC LIMIT 1"))).first()
    return {"total": total, "version": version[0] if version else None, "rows": [dict(r) for r in rows]}


async def dofbasen_stats(session: AsyncSession, artnr: int) -> dict:
    """Artssidens fund fra DOFbasen: antal, pr. måned og lokalafdeling, og alle fund for sjældne arter (≤ 400). Observationerne ligger i sin egen databasegruppe."""
    async def produce():
        ids = [r[0] for r in (await session.execute(text("SELECT artnr FROM species WHERE artnr = :a OR list_artnr = :a"), {"a": artnr})).all()]
        stat = (await session.execute(text("""SELECT count(*), count(DISTINCT (dato, loknr)), min(dato), max(dato) FROM observations WHERE artnr = ANY(:ids) AND hemmelig = 0"""), {"ids": ids})).first()
        months = [dict(r) for r in (await session.execute(text("""SELECT extract(month FROM dato)::int AS m, count(DISTINCT (dato, loknr)) AS n FROM observations
            WHERE artnr = ANY(:ids) AND hemmelig = 0 GROUP BY 1 ORDER BY 1"""), {"ids": ids})).mappings()]
        afd = [dict(r) for r in (await session.execute(text("""SELECT a.navn, count(DISTINCT (o.dato, o.loknr)) AS n FROM observations o JOIN locations l ON l.loknr = o.loknr
            JOIN afdelinger a ON a.id = l.afdeling_id WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 GROUP BY a.navn ORDER BY n DESC"""), {"ids": ids})).mappings()]
        fund = []
        if stat[1] and stat[1] <= 400:
            fund = [{**dict(r), "dato": r["dato"].isoformat()} for r in (await session.execute(text("""
                SELECT DISTINCT ON (o.dato, o.loknr) o.dato, o.loknr, l.navn AS lokalitet, o.antal, o.obsid,
                       trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observator
                FROM observations o LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN observers ob ON ob.obserkode = o.obserkode
                WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 ORDER BY o.dato DESC, o.loknr, o.antal DESC"""), {"ids": ids})).mappings()]
        return {"obs": stat[0], "fund": stat[1], "first": stat[2].isoformat() if stat[2] else None, "last": stat[3].isoformat() if stat[3] else None,
                "months": months, "afdelinger": afd, "fundliste": fund}
    return await cached(f"kl:art:{artnr}:{dk_today()}", 6 * 3600, produce)


async def species_page(session: AsyncSession, taxon_id: int, *, with_photos: bool) -> dict:
    t = (await session.execute(text("SELECT id, latin, dansk, engelsk, orden, familie, artnr, rang, netfugl_id FROM kl_taxa WHERE id = :i"), {"i": taxon_id})).mappings().first()
    if t is None:
        raise krydslister.KlError(404, "Arten findes ikke")
    entries = [dict(r) for r in (await session.execute(text("""
        SELECT l.key, l.navn, e.kategorier, e.sjaeldenhed FROM kl_species_list_entries e JOIN kl_species_lists l ON l.id = e.list_id WHERE e.taxon_id = :t ORDER BY l.sort"""), {"t": taxon_id})).mappings()]
    out = {"id": t["id"], "navn": t["dansk"] or t["engelsk"] or t["latin"], "latin": t["latin"], "engelsk": t["engelsk"], "orden": t["orden"], "familie": t["familie"],
           "artnr": t["artnr"], "lister": entries, "su": any(e["sjaeldenhed"] == "su" for e in entries)}
    if t["artnr"]:
        out["dofb"] = await dofbasen_stats(session, t["artnr"])
    if with_photos:
        out["fotos"] = await species_photos(session, t["id"], t["artnr"])
    ds = await krydslister.defs(session)
    out["kryds"] = []
    for key, d in ds.items():
        if d["param"] or not (d["auto"] or d["manuel"]):
            continue
        data = await krydslister.load_kryds(session, key)
        skey = krydslister._skey(d["ref"] == "dk", t["artnr"], t["latin"])
        n = sum(1 for r in data["rows"] if r["key"] == skey)
        if n:
            out["kryds"].append({"liste": key, "navn": data["name"], "key": skey, "antal": n, "deltagere": len({r["kode"] for r in data["rows"]})})
    return out


async def species_photos(session: AsyncSession, taxon_id: int, artnr: int | None, limit: int = 12) -> dict:
    """Publicerede fotos af arten fra fotogalleriet (gal_* ligger i brugergruppen, derfor et selvstændigt opslag)."""
    rows = (await session.execute(text("""
        SELECT p.id, m.thumb_key, m.storage_key FROM gal_photos p JOIN content_media m ON m.id = p.media_id
        WHERE p.status = 'publiceret' AND EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND (b.taxon_id = :t OR (CAST(:a AS int) IS NOT NULL AND b.artnr = :a)))
        ORDER BY p.dato DESC NULLS LAST, p.id DESC LIMIT :n"""), {"t": taxon_id, "a": artnr, "n": limit})).mappings().all()
    n = (await session.execute(text("""
        SELECT count(*) FROM gal_photos p WHERE p.status = 'publiceret' AND EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id
          AND (b.taxon_id = :t OR (CAST(:a AS int) IS NOT NULL AND b.artnr = :a)))"""), {"t": taxon_id, "a": artnr})).scalar() or 0
    return {"antal": n, "rows": [{"id": r["id"], "thumb": "/media/art/" + (r["thumb_key"] or r["storage_key"])} for r in rows], "artnr": artnr}
