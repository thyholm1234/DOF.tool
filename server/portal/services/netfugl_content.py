"""Netfugl-modulets indhold: dataadgang for forside, sider, links, artikler, kommentarer, likes, fotos, RSS, arter,
brugere, søgning, admin og API-nøgler (api/netfugl.py). Kontekst, rettigheder og krydslister ligger i services/netfugl.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached
from portal.models import NfApiGrant, NfApiToken, NfComment, NfKryds, NfLike, NfLink, NfPage, NfRole, User
from portal.timeutil import dk_today


async def rows(session: AsyncSession, sql: str, **args) -> list[dict]:
    return [dict(r) for r in (await session.execute(text(sql), args)).mappings().all()]


def iso(rs: list[dict]) -> list[dict]:
    """Datoer og tidspunkter som ISO-tekst (til JSON og cache)."""
    for r in rs:
        for k, v in r.items():
            if isinstance(v, (date, datetime)):
                r[k] = v.isoformat()
    return rs


# ---------------------------------------------------------------- fotos (bruges på mange sider)

PHOTO_COLS = """p.id, p.url, p.width, p.height, p.artnr, p.art_navn, p.latin, p.dato, p.lokalitet, p.loknr, p.land, p.alder,
    p.obsid, p.status, p.views, p.published_at, p.created_at, p.user_id, coalesce(u.navn, u.obserkode) AS fotograf, u.obserkode,
    (SELECT count(*) FROM nf_likes l WHERE l.target_kind = 'foto' AND l.target_id = p.id) AS likes,
    (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id AND NOT c.hidden) AS kommentarer"""
PHOTO_ORDER = {"nyeste": "p.published_at DESC", "likes": "likes DESC, p.published_at DESC", "visninger": "p.views DESC",
               "kommenteret": "(SELECT max(created_at) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id) DESC NULLS LAST"}


async def photo_list(session: AsyncSession, where: str = "TRUE", order: str = "p.published_at DESC", limit: int = 12, offset: int = 0,
                     **args) -> list[dict]:
    """Publicerede fotos. where/order er faste SQL-stumper fra koden (aldrig brugerinput); værdier går som parametre."""
    return iso(await rows(session, f"""SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id
        WHERE p.status = 'publiceret' AND {where} ORDER BY {order} LIMIT {int(limit)} OFFSET {int(offset)}""", **args))


async def species_photos(session: AsyncSession, artnr: int) -> list[dict]:
    """Fotos af arten og dens underarter."""
    return await photo_list(session, "(p.artnr = :a OR p.artnr IN (SELECT artnr FROM species WHERE list_artnr = :a))", a=artnr)


async def user_photos(session: AsyncSession, user_id: int, limit: int = 8) -> list[dict]:
    return await photo_list(session, "p.user_id = :u", u=user_id, limit=limit)


async def photos(session: AsyncSession, *, q: str = "", art: int | None = None, fotograf: int | None = None, su: bool = False,
                 sort: str = "nyeste", page: int = 1, per: int = 24) -> dict:
    where, args = ["TRUE"], {}
    if q.strip():
        where.append("(p.art_navn ILIKE :q OR p.latin ILIKE :q OR p.lokalitet ILIKE :q OR u.navn ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if art:
        where.append("(p.artnr = :art OR p.artnr IN (SELECT artnr FROM species WHERE list_artnr = :art))")
        args["art"] = art
    if fotograf:
        where.append("p.user_id = :fg")
        args["fg"] = fotograf
    if su:
        where.append("p.artnr IN (SELECT artnr FROM species WHERE klass = 'SU')")
    w = " AND ".join(where)
    total = (await session.execute(text(f"SELECT count(*) FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.status = 'publiceret' AND {w}"), args)).scalar()
    return {"total": total, "rows": await photo_list(session, w, PHOTO_ORDER[sort], per, (page - 1) * per, **args)}


async def photo_row(session: AsyncSession, pid: int) -> dict:
    """Ét foto med fotograf, likes og kommentartal (uanset status)."""
    return iso(await rows(session, f"SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.id = :i", i=pid))[0]


async def photos_by_status(session: AsyncSession, status: str) -> list[dict]:
    return iso(await rows(session, f"""SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.status = :s
        ORDER BY p.created_at DESC LIMIT 200""", s=status))


async def delete_photo_relations(session: AsyncSession, pid: int) -> None:
    """Kommentarer og likes på et foto, der slettes."""
    await session.execute(delete(NfComment).where(NfComment.target_kind == "foto", NfComment.target_id == pid))
    await session.execute(delete(NfLike).where(NfLike.target_kind == "foto", NfLike.target_id == pid))


async def owns_observation(session: AsyncSession, obsid: int, obserkode: str | None) -> bool:
    return bool((await session.execute(text("SELECT 1 FROM observations WHERE obsid = :o AND obserkode = :k"),
                                       {"o": obsid, "k": obserkode})).scalar())


async def user_obserkode(session: AsyncSession, user_id: int) -> str | None:
    return (await session.execute(select(User.obserkode).where(User.id == user_id))).scalar()


async def my_observations(session: AsyncSession, obserkode: str, artnr: int, dato: date | None) -> list[dict]:
    """Brugerens egne observationer af arten (± en dag fra datoen, ellers de seneste) – til at knytte et foto til DOFbasen."""
    where = "o.obserkode = :k AND (o.artnr = :a OR s.list_artnr = :a)"
    args = {"k": obserkode, "a": artnr}
    if dato:
        where += " AND o.dato BETWEEN :d1 AND :d2"
        args.update(d1=dato - timedelta(days=1), d2=dato + timedelta(days=1))
    return iso(await rows(session, f"""SELECT o.obsid, o.dato, o.antal, o.loknr, l.navn AS lokalitet, s.navn AS art
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE {where} ORDER BY o.dato DESC LIMIT 20""", **args))


# ---------------------------------------------------------------- arter i portalens artsliste

async def species_klass(session: AsyncSession, artnr: int) -> str | None:
    return (await session.execute(text("SELECT klass FROM species WHERE artnr = :a"), {"a": artnr})).scalar()


async def species_latin(session: AsyncSession, artnr: int) -> str | None:
    return (await session.execute(text("SELECT latin FROM species WHERE artnr = :a"), {"a": artnr})).scalar()


async def species_name_latin(session: AsyncSession, artnr: int):
    """(navn, latin) eller None."""
    return (await session.execute(text("SELECT navn, latin FROM species WHERE artnr = :a"), {"a": artnr})).first()


async def artnr_for_latin(session: AsyncSession, latin: str) -> int | None:
    return (await session.execute(text("SELECT artnr FROM species WHERE lower(latin) = lower(:l) ORDER BY art_type = 'art' DESC LIMIT 1"),
                                  {"l": latin})).scalar()


# ---------------------------------------------------------------- forside og fælles opslag

async def afdelinger_by_id(session: AsyncSession) -> list[dict]:
    return await rows(session, "SELECT id, navn FROM afdelinger ORDER BY id")


async def afdelinger_by_name(session: AsyncSession) -> list[dict]:
    return await rows(session, "SELECT id, navn FROM afdelinger ORDER BY navn")


async def kommuner(session: AsyncSession) -> list[dict]:
    return await rows(session, "SELECT kode, navn FROM kommuner ORDER BY navn")


async def front_articles(session: AsyncSession) -> dict:
    """Forsidens artikler, korte nyheder og seneste kommenterede artikler."""
    out = {"artikler": iso(await rows(session, """SELECT id, title, dato, writers, image_url, created_at FROM nf_articles
            WHERE published AND kind = 'artikel' ORDER BY dato DESC, id DESC LIMIT 3""")),
           "kort": iso(await rows(session, """SELECT a.id, a.title, a.dato, a.source_url,
            (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'artikel' AND c.target_id = a.id AND NOT c.hidden) AS kommentarer
            FROM nf_articles a WHERE published AND kind = 'kort' ORDER BY dato DESC, id DESC LIMIT 5"""))}
    comments = iso(await rows(session, """
            SELECT DISTINCT ON (a.id) a.id, a.title, c.created_at, coalesce(u.navn, u.obserkode) AS navn,
                   (SELECT count(*) FROM nf_comments x WHERE x.target_kind = 'artikel' AND x.target_id = a.id AND NOT x.hidden) AS antal
            FROM nf_comments c JOIN nf_articles a ON a.id = c.target_id JOIN users u ON u.id = c.user_id
            WHERE c.target_kind = 'artikel' AND NOT c.hidden AND a.published ORDER BY a.id, c.created_at DESC"""))
    out["kommentarer"] = sorted(comments, key=lambda r: r["created_at"], reverse=True)[:6]
    return out


async def front_photos(session: AsyncSession) -> dict:
    return {"fotos": await photo_list(session),
            "fotos_likes": await photo_list(session, "p.published_at > now() - interval '30 days'", "likes DESC, p.published_at DESC"),
            "fotos_kommenteret": await photo_list(session, """EXISTS (SELECT 1 FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id)""",
                                                  "(SELECT max(created_at) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id) DESC"),
            "fotos_su": await photo_list(session, "p.artnr IN (SELECT artnr FROM species WHERE klass = 'SU')")}


async def front_rss(session: AsyncSession) -> dict:
    out = {}
    for key, cond in (("rss_dk", "f.land = 'Danmark'"), ("rss_ud", "f.land <> 'Danmark'")):
        out[key] = iso(await rows(session, f"""SELECT i.title, i.url, i.published_at, f.navn AS kilde FROM nf_feed_items i
                JOIN nf_feeds f ON f.id = i.feed_id WHERE {cond} ORDER BY i.published_at DESC LIMIT 15"""))
    return out


async def rare_recent(session: AsyncSession, kind: str) -> list[dict]:
    """Seneste sjældne (SU, kind=rare) eller fåtallige (SUB) fugle i DOFbasen – 50 seneste, cachet 5 minutter."""
    kat = "SU" if kind == "rare" else "SUB"

    async def produce():
        return iso(await rows(session, """
            SELECT DISTINCT ON (o.dato, o.artnr, o.loknr) o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, o.obsid
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato > :d AND o.kategori = :k AND o.hemmelig = 0 ORDER BY o.dato DESC, o.artnr, o.loknr, o.obsid LIMIT 50
        """, d=dk_today() - timedelta(days=60), k=kat))
    return await cached(f"nf:sjf:{kind}:{dk_today()}:{datetime.now().hour}:{datetime.now().minute // 5}", 300, produce)


# ---------------------------------------------------------------- tekstsider og links

async def links(session: AsyncSession) -> list[NfLink]:
    return list((await session.execute(select(NfLink).order_by(NfLink.sort))).scalars())


async def replace_links(session: AsyncSession, items: list[dict]) -> None:
    """Erstat alle links (rækkefølgen bevares)."""
    await session.execute(delete(NfLink))
    for i, link in enumerate(items):
        session.add(NfLink(gruppe=link["gruppe"], titel=link["titel"], url=link["url"], sort=i))


async def pages(session: AsyncSession) -> list[NfPage]:
    return list((await session.execute(select(NfPage).order_by(NfPage.title))).scalars())


async def delete_page(session: AsyncSession, slug: str) -> None:
    await session.execute(delete(NfPage).where(NfPage.slug == slug))


# ---------------------------------------------------------------- artikler, kommentarer og likes

async def articles(session: AsyncSession, *, q: str = "", tag: str = "", aar: int | None = None, kind: str = "", page: int = 1,
                   include_unpublished: bool = False) -> tuple[int, list[dict]]:
    where, args = ["TRUE" if include_unpublished else "published"], {}
    if q.strip():
        where.append("(title ILIKE :q OR body ILIKE :q OR writers ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if tag:
        where.append(":tag = ANY(tags)")
        args["tag"] = tag
    if aar:
        where.append("extract(year FROM dato) = :aar")
        args["aar"] = aar
    if kind:
        where.append("kind = :kind")
        args["kind"] = kind
    w = " AND ".join(where)
    total = (await session.execute(text(f"SELECT count(*) FROM nf_articles WHERE {w}"), args)).scalar()
    rs = iso(await rows(session, f"""SELECT a.id, a.kind, a.title, a.dato, a.writers, a.tags, a.image_url, a.published, a.views,
        left(regexp_replace(a.body, '\\[([^\\]]*)\\]\\([^)]*\\)', '\\1', 'g'), 240) AS teaser,
        (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'artikel' AND c.target_id = a.id AND NOT c.hidden) AS kommentarer
        FROM nf_articles a WHERE {w} ORDER BY dato DESC, id DESC LIMIT 20 OFFSET :off""", **args, off=(page - 1) * 20))
    return total, rs


async def tags_and_years(session: AsyncSession) -> dict:
    tags = await rows(session, "SELECT t AS tag, count(*) AS n FROM nf_articles, unnest(tags) t WHERE published GROUP BY t ORDER BY t")
    years = [r[0] for r in (await session.execute(text("SELECT DISTINCT extract(year FROM dato)::int FROM nf_articles WHERE published ORDER BY 1 DESC"))).all()]
    return {"tags": tags, "years": years}


async def comments(session: AsyncSession, kind: str, target: int, user_id: int, can_moderate: bool) -> list[dict]:
    """Kommentarer på en artikel eller et foto. Skjulte vises kun for moderatorer og forfatteren selv."""
    rs = iso(await rows(session, """
        SELECT c.id, c.body, c.created_at, c.hidden, c.user_id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode,
               (SELECT count(*) FROM nf_likes l WHERE l.target_kind = 'komm' AND l.target_id = c.id) AS likes
        FROM nf_comments c JOIN users u ON u.id = c.user_id WHERE c.target_kind = :k AND c.target_id = :t ORDER BY c.created_at
    """, k=kind, t=target))
    for r in rs:
        r["mine"] = r["user_id"] == user_id
        if r["hidden"] and not can_moderate and not r["mine"]:
            r["body"] = "(Kommentaren er skjult af en moderator)"
    return rs


async def delete_article_comments(session: AsyncSession, aid: int) -> None:
    await session.execute(delete(NfComment).where(NfComment.target_kind == "artikel", NfComment.target_id == aid))


async def like_count(session: AsyncSession, kind: str, target: int) -> int:
    return (await session.execute(text("SELECT count(*) FROM nf_likes WHERE target_kind = :k AND target_id = :t"), {"k": kind, "t": target})).scalar()


async def all_comments(session: AsyncSession) -> list[dict]:
    """De seneste 200 kommentarer til moderation."""
    return iso(await rows(session, """SELECT c.id, c.target_kind, c.target_id, c.body, c.hidden, c.created_at, coalesce(u.navn, u.obserkode) AS navn,
        CASE WHEN c.target_kind = 'artikel' THEN (SELECT title FROM nf_articles WHERE id = c.target_id)
             ELSE (SELECT art_navn FROM nf_photos WHERE id = c.target_id) END AS emne
        FROM nf_comments c JOIN users u ON u.id = c.user_id ORDER BY c.created_at DESC LIMIT 200"""))


# ---------------------------------------------------------------- RSS

async def rss_items(session: AsyncSession, *, q: str = "", kilde: int | None = None, land: str = "", page: int = 1) -> dict:
    where, args = ["TRUE"], {}
    if q.strip():
        where.append("(i.title ILIKE :q OR i.summary ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if kilde:
        where.append("f.id = :f")
        args["f"] = kilde
    if land:
        where.append("f.land = :land")
        args["land"] = land
    w = " AND ".join(where)
    total = (await session.execute(text(f"SELECT count(*) FROM nf_feed_items i JOIN nf_feeds f ON f.id = i.feed_id WHERE {w}"), args)).scalar()
    rs = iso(await rows(session, f"""SELECT i.id, i.title, i.url, i.summary, i.published_at, f.navn AS kilde, f.land FROM nf_feed_items i
        JOIN nf_feeds f ON f.id = i.feed_id WHERE {w} ORDER BY i.published_at DESC LIMIT 30 OFFSET :off""", **args, off=(page - 1) * 30))
    feeds = await rows(session, "SELECT id, navn, land FROM nf_feeds ORDER BY navn")
    return {"total": total, "rows": rs, "kilder": feeds, "lande": sorted({f["land"] for f in feeds})}


async def feeds(session: AsyncSession) -> list[dict]:
    return iso(await rows(session, "SELECT id, navn, site_url, feed_url, land, active, last_fetch, last_error, items FROM nf_feeds ORDER BY navn"))


# ---------------------------------------------------------------- krydslister (manuelle kryds)

async def aedel_years(session: AsyncSession) -> list[int]:
    return [r[0] for r in (await session.execute(text(
        "SELECT DISTINCT extract(year FROM dato)::int FROM nf_kryds WHERE liste = 'aedel' AND dato IS NOT NULL ORDER BY 1 DESC"))).all()]


async def hidden_kryds(session: AsyncSession, obserkode: str, liste: str) -> list[dict]:
    return iso(await rows(session, "SELECT id, artnr, navn, latin FROM nf_kryds WHERE obserkode = :k AND liste = :l AND hidden",
                          k=obserkode, l=liste))


async def delete_kryds_for_species(session: AsyncSession, obserkode: str, liste: str, latin: str) -> None:
    await session.execute(delete(NfKryds).where(NfKryds.obserkode == obserkode, NfKryds.liste == liste, NfKryds.latin == latin))


async def delete_hidden_dk(session: AsyncSession, obserkode: str, artnr: int) -> None:
    await session.execute(delete(NfKryds).where(NfKryds.obserkode == obserkode, NfKryds.liste == "dk", NfKryds.artnr == artnr,
                                                NfKryds.hidden))


# ---------------------------------------------------------------- arter

async def families(session: AsyncSession) -> list[dict]:
    return await rows(session, "SELECT id, orden, familie, engelsk, antal FROM nf_families ORDER BY id")


async def species_list(session: AsyncSession, liste: str, q: str = "") -> list[dict]:
    where, args = "liste = :l", {"l": liste}
    if q.strip():
        where += " AND (navn ILIKE :q OR latin ILIKE :q OR engelsk ILIKE :q)"
        args["q"] = f"%{q.strip()}%"
    return await rows(session, f"SELECT id, sort, su, navn, latin, engelsk, kategori, artnr FROM nf_species WHERE {where} ORDER BY sort", **args)


async def dofbasen_stats(session: AsyncSession, artnr: int) -> dict:
    """Artssidens DK-fund fra DOFbasen: antal, pr. måned og lokalafdeling, og alle fund for sjældne arter (≤ 400)."""
    async def produce():
        ids = [r[0] for r in (await session.execute(text("SELECT artnr FROM species WHERE artnr = :a OR list_artnr = :a"), {"a": artnr})).all()]
        stat = (await session.execute(text("""SELECT count(*), count(DISTINCT (dato, loknr)), min(dato), max(dato)
                FROM observations WHERE artnr = ANY(:ids) AND hemmelig = 0"""), {"ids": ids})).first()
        months = await rows(session, """SELECT extract(month FROM dato)::int AS m, count(DISTINCT (dato, loknr)) AS n FROM observations
                WHERE artnr = ANY(:ids) AND hemmelig = 0 GROUP BY 1 ORDER BY 1""", ids=ids)
        afd = await rows(session, """SELECT a.navn, count(DISTINCT (o.dato, o.loknr)) AS n FROM observations o JOIN locations l ON l.loknr = o.loknr
                JOIN afdelinger a ON a.id = l.afdeling_id WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 GROUP BY a.navn ORDER BY n DESC""", ids=ids)
        fund = []
        if stat[1] and stat[1] <= 400:
            fund = iso(await rows(session, """SELECT DISTINCT ON (o.dato, o.loknr) o.dato, o.loknr, l.navn AS lokalitet, o.antal, o.obsid,
                    trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observator
                    FROM observations o LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN observers ob ON ob.obserkode = o.obserkode
                    WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 ORDER BY o.dato DESC, o.loknr, o.antal DESC""", ids=ids))
        return {"obs": stat[0], "fund": stat[1], "first": stat[2].isoformat() if stat[2] else None,
                "last": stat[3].isoformat() if stat[3] else None, "months": months, "afdelinger": afd, "fundliste": fund}
    return await cached(f"nf:art:{artnr}:{dk_today()}", 6 * 3600, produce)


# ---------------------------------------------------------------- brugere og søgning

async def user_by_obserkode(session: AsyncSession, kode: str) -> User | None:
    return (await session.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()


async def published_photo_counts(session: AsyncSession) -> dict[str, int]:
    return {r["obserkode"]: r["n"] for r in await rows(session, """SELECT u.obserkode, count(*) AS n FROM nf_photos p JOIN users u ON u.id = p.user_id
        WHERE p.status = 'publiceret' GROUP BY u.obserkode""")}


async def user_photo_stats(session: AsyncSession, user_id: int) -> dict:
    st = (await session.execute(text("""SELECT count(*), max(published_at), coalesce(sum(views), 0), coalesce(max(views), 0)
            FROM nf_photos WHERE user_id = :u AND status = 'publiceret'"""), {"u": user_id})).first()
    return {"antal": st[0], "seneste": st[1].isoformat() if st[1] else None, "visninger": st[2], "hoejeste": st[3], "user_id": user_id}


async def search(session: AsyncSession, q: str, *, articles_on: bool, photos_on: bool, rss_on: bool) -> dict:
    like = f"%{q.strip()}%"
    out = {"arter": await rows(session, """SELECT id, liste, navn, latin, su FROM nf_species WHERE navn ILIKE :q OR latin ILIKE :q OR engelsk ILIKE :q
        ORDER BY liste, sort LIMIT 30""", q=like)}
    if articles_on:
        out["artikler"] = iso(await rows(session, """SELECT id, title, dato, kind FROM nf_articles WHERE published AND (title ILIKE :q OR body ILIKE :q)
            ORDER BY dato DESC LIMIT 20""", q=like))
    if photos_on:
        out["fotos"] = await photo_list(session, "(p.art_navn ILIKE :q OR p.lokalitet ILIKE :q OR u.navn ILIKE :q)", limit=12, q=like)
    if rss_on:
        out["rss"] = iso(await rows(session, """SELECT i.title, i.url, i.published_at, f.navn AS kilde FROM nf_feed_items i JOIN nf_feeds f ON f.id = i.feed_id
            WHERE i.title ILIKE :q OR i.summary ILIKE :q ORDER BY i.published_at DESC LIMIT 15""", q=like))
    return out


# ---------------------------------------------------------------- admin

async def admin_counts(session: AsyncSession) -> dict:
    return (await rows(session, """SELECT (SELECT count(*) FROM nf_articles) AS artikler, (SELECT count(*) FROM nf_comments) AS kommentarer,
        (SELECT count(*) FROM nf_photos WHERE status = 'publiceret') AS fotos, (SELECT count(*) FROM nf_photos WHERE status = 'afventer') AS afventer,
        (SELECT count(*) FROM nf_feeds WHERE active) AS kilder, (SELECT count(*) FROM nf_feed_items) AS rss,
        (SELECT count(DISTINCT obserkode) FROM nf_auto_lists) AS deltagere,
        (SELECT coalesce(sum(cardinality(artnr)), 0) FROM nf_auto_lists) AS auto_kryds,
        (SELECT count(*) FROM nf_kryds) AS manuelle_kryds, (SELECT count(*) FROM nf_species WHERE liste = 'dk') AS dk_arter,
        (SELECT count(*) FROM nf_species WHERE liste = 'vp') AS vp_arter, (SELECT count(*) FROM nf_user_roles) AS roller,
        (SELECT count(*) FROM nf_api_grants) AS api_brugere, (SELECT coalesce(sum(uses), 0) FROM nf_api_tokens) AS api_kald"""))[0]


async def audit_log(session: AsyncSession) -> list[dict]:
    return iso(await rows(session, """SELECT a.at, a.action, a.target, u.navn, u.obserkode FROM nf_audit a LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.at DESC LIMIT 40"""))


async def roles(session: AsyncSession) -> list[NfRole]:
    return list((await session.execute(select(NfRole).order_by(NfRole.sort, NfRole.name))).scalars())


async def role_holders(session: AsyncSession) -> list[dict]:
    return iso(await rows(session, """SELECT u.id, u.navn, u.obserkode, array_agg(ur.role_key ORDER BY ur.role_key) AS roles, max(ur.granted_at) AS granted_at
        FROM nf_user_roles ur JOIN users u ON u.id = ur.user_id GROUP BY u.id ORDER BY u.navn"""))


async def delete_role(session: AsyncSession, key: str) -> None:
    await session.execute(delete(NfRole).where(NfRole.key == key))


async def find_users(session: AsyncSession, q: str) -> list[dict]:
    """Godkendte brugere til tildeling af roller og API-adgang, med deres Netfugl-roller."""
    return await rows(session, """SELECT u.id, u.navn, u.obserkode,
        coalesce((SELECT array_agg(role_key) FROM nf_user_roles WHERE user_id = u.id), '{}') AS roles,
        EXISTS (SELECT 1 FROM nf_api_grants g WHERE g.user_id = u.id) AS api
        FROM users u WHERE (u.navn ILIKE :q OR u.obserkode ILIKE :q) AND u.approved AND NOT u.blocked ORDER BY u.navn LIMIT 20""", q=f"%{q}%")


# ---------------------------------------------------------------- API-adgang og nøgler

async def api_grants(session: AsyncSession) -> list[dict]:
    return iso(await rows(session, """SELECT g.user_id, u.navn, u.obserkode, g.rate_per_min, g.max_tokens, g.note, g.granted_at, gb.navn AS granted_by,
        (SELECT count(*) FROM nf_api_tokens t WHERE t.user_id = g.user_id AND t.revoked_at IS NULL) AS tokens,
        (SELECT coalesce(sum(uses), 0) FROM nf_api_tokens t WHERE t.user_id = g.user_id) AS uses,
        (SELECT max(last_used_at) FROM nf_api_tokens t WHERE t.user_id = g.user_id) AS last_used
        FROM nf_api_grants g JOIN users u ON u.id = g.user_id LEFT JOIN users gb ON gb.id = g.granted_by ORDER BY u.navn"""))


async def api_tokens(session: AsyncSession) -> list[dict]:
    return iso(await rows(session, """SELECT t.id, t.user_id, u.obserkode, t.name, t.prefix, t.created_at, t.last_used_at, t.uses, t.revoked_at
        FROM nf_api_tokens t JOIN users u ON u.id = t.user_id ORDER BY t.created_at DESC LIMIT 300"""))


async def revoke_access(session: AsyncSession, user_id: int) -> None:
    """Fjern brugerens API-adgang og spær alle aktive nøgler."""
    await session.execute(delete(NfApiGrant).where(NfApiGrant.user_id == user_id))
    await session.execute(update(NfApiToken).where(NfApiToken.user_id == user_id, NfApiToken.revoked_at.is_(None)).values(revoked_at=func.now()))


async def revoke_token(session: AsyncSession, token_id: int, user_id: int | None = None) -> None:
    """Spær en nøgle (med user_id kun, hvis den er brugerens egen)."""
    cond = [NfApiToken.id == token_id, NfApiToken.revoked_at.is_(None)]
    if user_id is not None:
        cond.append(NfApiToken.user_id == user_id)
    await session.execute(update(NfApiToken).where(*cond).values(revoked_at=func.now()))


async def my_tokens(session: AsyncSession, user_id: int) -> list[NfApiToken]:
    return list((await session.execute(select(NfApiToken).where(NfApiToken.user_id == user_id).order_by(NfApiToken.created_at.desc()))).scalars())


async def active_token_count(session: AsyncSession, user_id: int) -> int:
    return (await session.execute(select(func.count()).select_from(NfApiToken)
                                  .where(NfApiToken.user_id == user_id, NfApiToken.revoked_at.is_(None)))).scalar()
