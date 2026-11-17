"""Levering af alarmer: alle gemmes i én transaktion, og push til alle brugere/enheder sendes samtidig (lav roundtrip).

DB-delen springes over, medmindre ARTICLES_TEST_DSN er sat (se tests/test_articles_db.py for engangscontainerne)."""

import asyncio
import time

import pytest
from sqlalchemy import text

from test_photo_import import DSN, db  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


def _msg(title, push=True):
    return {"title": title, "body": "b", "url": "/#/", "tag": title, "kategori": "SU", "rule_id": None, "push": push}


async def test_deliver_sender_alle_push_samtidig(db, monkeypatch):  # noqa: F811
    from portal.services import alerts, fcm, push

    async with db() as s:
        await s.execute(text("""INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES
            (1, 'fcm:tok-1', '', ''), (1, 'https://push.example/w1', 'k', 'a'),
            (2, 'fcm:dead', '', ''), (3, 'https://push.example/w3', 'k', 'a')"""))
        await s.commit()

    async def no_vapid(session):
        return "pub"

    async def no_fcm(session):
        return None

    calls = []

    async def fake_fcm(items):
        calls.append(("fcm", [t for t, _ in items]))
        await asyncio.sleep(0.1)
        return [410 if t == "dead" else 200 for t, _ in items]

    async def fake_web(sub, payload):
        calls.append(("web", sub.endpoint))
        await asyncio.sleep(0.1)
        return 201

    monkeypatch.setattr(push, "ensure_vapid", no_vapid)
    monkeypatch.setattr(fcm, "ensure", no_fcm)
    monkeypatch.setattr(fcm, "enabled", lambda: True)
    monkeypatch.setattr(fcm, "send_many", fake_fcm)
    monkeypatch.setattr(push, "_web", fake_web)

    outbox = {(1, "a"): _msg("A"), (1, "b"): _msg("B"), (2, "c"): _msg("C"), (3, "d"): _msg("D"), (4, "e"): _msg("E", push=False)}
    t0 = time.time()
    async with db() as s:
        sent = await alerts.deliver(s, None, outbox)
    assert time.time() - t0 < 0.35  # seks enheder à 0,1 s ville tage 0,6 s i serie
    assert sent == 3  # A og B (bruger 1) og D (bruger 3); C gik til et udløbet token, E er uden push
    assert [c for c in calls if c[0] == "fcm"] == [("fcm", ["tok-1", "tok-1", "dead"])]
    async with db() as s:
        rows = dict((await s.execute(text("SELECT title, pushed FROM notifications"))).all())
        assert rows == {"A": True, "B": True, "C": False, "D": True, "E": False}
        eps = set((await s.execute(text("SELECT endpoint FROM push_subscriptions"))).scalars())
        assert "fcm:dead" not in eps and len(eps) == 3
        assert (await s.execute(text("SELECT count(*) FROM push_subscriptions WHERE last_ok IS NOT NULL"))).scalar() == 3
        # Samme dedup-nøgle igen: intet nyt gemmes, intet sendes
        calls.clear()
        assert await alerts.deliver(s, None, outbox) == 0 and calls == []


def _tool_obs(**kw):
    """En DOF.tool-indtastning som den ligger i DOF.tool-gruppens datasæt (services/doftool_obs.py)."""
    return {"obsid": -1_000_000_007, "turid": -1_000_000_003, "dato": "2026-10-27", "artnr": 1, "antal": 9, "loknr": 100,
            "obserkode": "A2", "hemmelig": 0, "kategori": "alm", "afdelinger": [], "indtastning": "doftool", **kw}


async def test_doftool_indtastninger_taeller_som_dofbasen_i_alarmer(db, monkeypatch):  # noqa: F811
    """Trådens maksimum og observatører, dagens frø og 'arter jeg mangler' ser DOF.tool-indtastninger som DOFbasens."""
    from datetime import date

    from portal.cache import kv
    from portal.services import alerts, dataset, lists
    from portal.services.areas import Scope
    from portal.services.lists import ListSpec

    rows = [_tool_obs(), _tool_obs(obsid=-1_000_000_008, artnr=2, antal=50, hemmelig=1)]  # skjult: tæller ikke for andre

    async def tool_data():
        return {"obs": rows, "trips": []}

    monkeypatch.setattr(dataset, "tool_data", tool_data)
    monkeypatch.setattr(lists, "countable_sql", lambda: "TRUE")  # artskategorierne (A/B/C) er ikke i testdatabasen
    day = date(2026, 10, 27)
    async with db() as s:
        mx, observers = await alerts._thread_state(s, {(day, 1, 100), (day, 2, 100)})
        assert mx[(day, 1, 100)] == 9  # DOFbasen har 2 (LB) og 1 (A1) – DOF.tool-indtastningen på 9 er størst
        assert observers[(day, 1, 100)] == {"LB", "A1", "A2"}  # A2 får ikke besked om en tråd, A2 selv har meldt i
        assert (day, 2, 100) not in mx

        await alerts.seed_threads(s, day)
        seeded = await kv().hgetall(f"alert:threadmax:{day}")
        assert seeded == {"1:100": "9"}

        await s.execute(text("UPDATE species SET list_artnr = artnr"))
        crossed = alerts.Crossed(s)
        assert 1 in await crossed.species("A2", ListSpec(Scope("dk"), None))  # krydset via DOF.tool, som i Lister
        assert 2 in await crossed.species("A2", ListSpec(Scope("dk"), 2026))  # egen skjult indtastning tæller på egen liste
        assert await crossed.species("XX", ListSpec(Scope("dk"), None)) == set()
