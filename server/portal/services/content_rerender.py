"""Render alt igen (artikler, nyheder, kommentarer), når rendererens regler er ændret (``content_render.RENDER_VERSION``), og genberegn arter."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump
from portal.services import article_species, content_render


async def rerender_all(session: AsyncSession) -> dict:
    out = {"artikler": 0, "nyheder": 0, "kommentarer": 0}
    arts = (await session.execute(text("SELECT id FROM art_articles ORDER BY id"))).scalars().all()
    for n, aid in enumerate(arts, 1):
        md = (await session.execute(text("SELECT body_md FROM art_articles WHERE id = :i"), {"i": aid})).scalar() or ""
        r = await content_render.render_for(session, md, "artikel")
        await session.execute(text("UPDATE art_articles SET body_html = :h, body_text = :t, render_version = :v WHERE id = :i"),
                              {"h": r.html, "t": r.text, "v": content_render.RENDER_VERSION, "i": aid})
        out["artikler"] += 1
        if n % 50 == 0:
            await session.commit()
            await jobstatus.step(f"Artikel {n} af {len(arts)}", n, len(arts))
    for nid, body in (await session.execute(text("SELECT id, body FROM news"))).all():
        r = await content_render.render_for(session, body, "nyhed")
        await session.execute(text("UPDATE news SET body_html = :h, render_version = :v WHERE id = :i"), {"h": r.html, "v": content_render.RENDER_VERSION, "i": nid})
        out["nyheder"] += 1
    for cid, body in (await session.execute(text("SELECT id, body_md FROM art_comments"))).all():
        await session.execute(text("UPDATE art_comments SET body_html = :h WHERE id = :i"), {"h": content_render.render(body, "kommentar").html, "i": cid})
        out["kommentarer"] += 1
    await session.commit()
    await bump("articles")
    return out


async def reindex_species(session: AsyncSession) -> int:
    """Genberegn arterne i alle artikler (efter ændret artsliste, tvetydige navne eller emneord)."""
    matcher = await article_species.load_matcher(session, fresh=True)
    ids = (await session.execute(text("SELECT id FROM art_articles ORDER BY id"))).scalars().all()
    for n, aid in enumerate(ids, 1):
        await article_species.index_article(session, aid, matcher)
        if n % 100 == 0:
            await session.commit()
            await jobstatus.step(f"Artikel {n} af {len(ids)}", n, len(ids))
    await session.commit()
    await bump("articles")
    return len(ids)
