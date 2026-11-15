"""Artiklernes Snak-opslag (services/snak_articles): oprettelse, tekst, status, arkivet og det fælles kommentarspor.

Uddraget testes uden database; resten kører mod Postgres og Valkey som test_articles_db.py (ARTICLES_TEST_DSN)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from test_articles_db import DSN, db  # noqa: F401 – db er en fixture

from portal.services.snak_articles import _author_name, excerpt

needs_db = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


def test_excerpt_cuts_at_a_word_and_falls_back_to_the_lead():
    assert excerpt("Kort tekst.") == "Kort tekst."
    assert excerpt("  flere   mellemrum\n\nog linjer ") == "flere mellemrum og linjer"
    long = "Silkehalerne kom i tusindvis. " * 20
    ex = excerpt(long, n=100)
    assert ex.endswith(" …") and len(ex) <= 102 and not ex[:-2].endswith((" ", ".")) and long.startswith(ex[:-2])
    assert excerpt("", "Manchetten") == "Manchetten"
    assert excerpt("x" * 500, n=50) == "x" * 50 + " …"  # intet mellemrum: skæres hårdt


def test_author_name_is_shortened():
    assert _author_name(None) == "Artikler"
    assert _author_name("A, B") == "A, B"
    many = ", ".join(f"Forfatter nummer {i}" for i in range(10))
    assert len(_author_name(many)) <= 80 and _author_name(many).endswith(" m.fl.")


async def _setup(s):
    from portal.services import features, snak

    await snak.seed_categories(s)
    await features.save(s, {"artikler": "on"})  # Snak selv er slået fra: kommentarsporet følger artiklerne


async def _post(s, aid):
    return (await s.execute(text("SELECT * FROM sn_posts WHERE article_id = :a"), {"a": aid})).mappings().first()


@needs_db
async def test_post_follows_the_article(db):  # noqa: F811
    from portal.models import User
    from portal.services import article_edit

    async with db() as s:
        await _setup(s)
        ed = await s.get(User, 3)
        art, _ = await article_edit.save(s, ed, {"title": "Kladden", "body_md": "tekst", "status": "kladde"})
        assert await _post(s, art.id) is None  # ingen opslag til en kladde
        body = "Første afsnit om [[art:1]] ved stranden.\n\n" + "Mere tekst. " * 60
        data = {"title": "Dværgterner i år", "body_md": body, "status": "publiceret", "authors": [{"navn": "Anne Skribent"}, {"navn": "Bo Fotograf"}]}
        art, _ = await article_edit.save(s, ed, data, art)
        p = await _post(s, art.id)
        assert p["titel"] == "Dværgterner i år" and p["user_id"] is None and p["eksempel_navn"] == "Anne Skribent, Bo Fotograf"
        assert p["body_text"].startswith("Første afsnit om Dværgterne") and p["body_text"].endswith(" …") and p["status"] == "synlig"
        assert p["attachment"]["type"] == "artikel" and p["attachment"]["url"] == f"#/artikler/{art.id}" and 1 in p["arter"]
        cat = (await s.execute(text("SELECT slug FROM sn_categories WHERE id = :c"), {"c": p["category_id"]})).scalar()
        assert cat == "artikler" and not p["locked"]
        # redaktørens tekst til opslaget (markdown) og lukkede kommentarer
        art, _ = await article_edit.save(s, ed, {**data, "snak_tekst": "Hvorfor **så mange**?", "comments_open": False, "version": art.version}, art)
        p2 = await _post(s, art.id)
        assert p2["id"] == p["id"] and p2["body_md"] == "Hvorfor **så mange**?" and "<strong>" in p2["body_html"] and p2["locked"]
        # trukket tilbage → skjult; publiceret igen → synlig. En moderators skjulning respekteres.
        art, _ = await article_edit.save(s, ed, {**data, "status": "skjult", "version": art.version}, art)
        assert (await _post(s, art.id))["status"] == "skjult"
        art, _ = await article_edit.save(s, ed, {**data, "version": art.version}, art)
        assert (await _post(s, art.id))["status"] == "synlig"
        await s.execute(text("UPDATE sn_posts SET status = 'skjult', hidden_reason = 'Regelbrud', hidden_by = 4 WHERE id = :p"), {"p": p["id"]})
        await s.commit()
        art, _ = await article_edit.save(s, ed, {**data, "version": art.version}, art)
        assert (await _post(s, art.id))["status"] == "skjult"
        # slettes artiklen, forsvinder opslaget
        await article_edit.delete_article(s, art)
        assert (await s.execute(text("SELECT count(*) FROM sn_posts"))).scalar() == 0


@needs_db
async def test_backfill_dates_archive_posts_and_moves_follows(db):  # noqa: F811
    from portal.services import snak_articles

    async with db() as s:
        await _setup(s)
        old = datetime(2012, 5, 1, 10, tzinfo=UTC)
        ids = []
        for i, status in enumerate(["publiceret", "publiceret", "kladde"]):
            ids.append((await s.execute(text("""INSERT INTO art_articles (kind, title, slug, body_md, body_html, body_text, dato, published_at, status, source, authors,
                                                                       import_errors, sprog, views, views_imported, version, render_version, comments_open)
                VALUES ('artikel', :t, '', '', '', :b, :d, now(), :st, 'netfugl', '[]', '[]', 'da', 0, 0, 1, 0, true) RETURNING id"""),
                {"t": f"Arkiv {i}", "b": f"Gammel tekst {i}", "d": old, "st": status})).scalar())
        await s.execute(text("INSERT INTO art_subscriptions (user_id, kind, ref, via, muted) VALUES (1, 'artikel', :a, 'manuel', false)"), {"a": ids[0]})
        await s.commit()
        assert await snak_articles.backfill(s) == 2
        assert await snak_articles.backfill(s) == 0  # idempotent
        p = await _post(s, ids[0])
        assert p["created_at"] == old and p["last_activity_at"] == old and p["body_text"] == "Gammel tekst 0" and p["eksempel_navn"] == "Artikler"
        assert await _post(s, ids[2]) is None
        assert (await s.execute(text("SELECT user_id FROM sn_follows WHERE post_id = :p"), {"p": p["id"]})).scalar() == 1
        assert (await s.execute(text("SELECT count(*) FROM art_subscriptions WHERE kind = 'artikel'"))).scalar() == 0


@needs_db
async def test_comment_on_article_post_notifies_with_article_link(db):  # noqa: F811
    from portal.models import User
    from portal.services import article_edit, article_subs, snak_comments

    async with db() as s:
        await _setup(s)
        ed = await s.get(User, 3)
        art, _ = await article_edit.save(s, ed, {"title": "Debat", "body_md": "tekst", "status": "publiceret"})
        pid = (await _post(s, art.id))["id"]
        assert await article_subs.follow(s, 1, art.id, True)
        assert [f["id"] for f in (await article_subs.get_prefs(s, 1))["foelger"]] == [art.id]
        c = await snak_comments.add(s, await s.get(User, 2), pid, "Godt skrevet", None, staff=False)
        r = await snak_comments.add(s, await s.get(User, 1), pid, "Tak", c.id, staff=False)
        notes = (await s.execute(text("SELECT user_id, url FROM notifications ORDER BY id"))).all()
        assert (1, f"/#/artikler/{art.id}?sk={c.id}") in notes  # følger artiklen
        assert (2, f"/#/artikler/{art.id}?sk={r.id}") in notes  # svar
        n = (await s.execute(text("SELECT n_comments FROM sn_posts WHERE id = :p"), {"p": pid})).scalar()
        assert n == 2
        from portal.services import articles

        assert (await articles.detail(s, art.id, can_edit=False, user_id=1))["kommentarer"] == 2
        # valget «nye kommentarer på artikler» slår beskeder om fulgte artikler fra
        await article_subs.set_prefs(s, 1, None, False)
        await snak_comments.add(s, await s.get(User, 2), pid, "Igen", None, staff=False)
        assert len([u for u, _ in (await s.execute(text("SELECT user_id, url FROM notifications"))).all() if u == 1]) == 1
