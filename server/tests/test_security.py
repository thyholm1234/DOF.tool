"""Sikkerhedsregressioner fra sikkerhedsgennemgangen 2026-11-08 (docs/sikkerhed/gennemgang-2026-11-08.md).

Kører uden database og Valkey: Valkey erstattes af en lille udgave i hukommelsen.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest


class FakeKV:
    """Det udsnit af Valkey/redis-asyncio, koden her bruger."""

    def __init__(self):
        self.data: dict[str, object] = {}

    async def get(self, k):
        v = self.data.get(k)
        return v if isinstance(v, str) or v is None else str(v)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.data:
            return None
        self.data[k] = str(v)
        return True

    async def incr(self, k):
        self.data[k] = int(self.data.get(k, 0)) + 1
        return self.data[k]

    async def expire(self, k, ttl):
        return True

    async def ttl(self, k):
        return 100

    async def exists(self, k):
        return int(k in self.data)

    async def delete(self, *ks):
        n = sum(1 for k in ks if k in self.data)
        for k in ks:
            self.data.pop(k, None)
        return n

    async def sadd(self, k, *vs):
        self.data.setdefault(k, set()).update(vs)

    async def srem(self, k, *vs):
        self.data.get(k, set()).difference_update(vs)

    async def smembers(self, k):
        return set(self.data.get(k, set()))

    async def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        for k in list(self.data):
            if k.startswith(prefix):
                yield k


@pytest.fixture
def fake_kv(monkeypatch):
    from portal.services import account, auth

    kv = FakeKV()
    monkeypatch.setattr(auth, "kv", lambda: kv)
    monkeypatch.setattr(account, "kv", lambda: kv)
    return kv


# ---------------------------------------------------------------- S-01: sessioner i klartekst


async def test_gammel_klartekst_session_godtages_ikke(fake_kv):
    from portal.services import auth

    fake_kv.data["sess:gammeltoken"] = "7"
    assert await auth.session_user_id("gammeltoken") is None
    assert "sess:gammeltoken" in fake_kv.data  # opslaget flytter den ikke over til en gyldig hashet session


async def test_hashet_session_virker(fake_kv):
    from portal.services import auth

    fake_kv.data[auth.session_key("nyttoken")] = "7"
    assert await auth.session_user_id("nyttoken") == "7"


async def test_purge_sletter_klartekst_og_beholder_hashede(fake_kv):
    from portal.services import auth

    hashed = auth.session_key("nyt")
    fake_kv.data.update({"sess:gammel1": "7", "sess:gammel2": "8", hashed: "7", "usess:7": {"gammel1", hashed}})
    assert await auth.purge_legacy_sessions() == 2
    assert "sess:gammel1" not in fake_kv.data and "sess:gammel2" not in fake_kv.data
    assert fake_kv.data[hashed] == "7"
    assert fake_kv.data["usess:7"] == {hashed}
    assert await auth.purge_legacy_sessions() == 0  # gør intet anden gang


async def test_destroy_sessions_sletter_hashede(fake_kv):
    from portal.services import account, auth

    token = await auth.create_session(type("U", (), {"id": 9})())
    await account.destroy_sessions(9)
    assert await auth.session_user_id(token) is None


# ---------------------------------------------------------------- S-04: loft over loginforsøg pr. time


async def test_loginloft_pr_time_gaelder_uanset_ip(fake_kv):
    from portal.services import auth

    fake_kv.data["login:try:h:ABC1"] = auth.LOGIN_PER_HOUR
    with pytest.raises(auth.AuthError) as exc:
        await auth._rate_limit("ABC1", "10.9.8.7")  # ny IP og intet minuttal – timeloftet stopper alligevel
    assert exc.value.status == 429


async def test_loginloft_pr_minut(fake_kv):
    from portal.services import auth

    for i in range(auth.LOGIN_PER_MIN):
        await auth._rate_limit("ABC2", f"10.0.0.{i}")
    with pytest.raises(auth.AuthError):
        await auth._rate_limit("ABC2", "10.0.0.99")


# ---------------------------------------------------------------- S-05: vagtens undtagelser er præcise


def test_public_api_er_praecis():
    from portal.main import public_api

    assert public_api("/api/me")
    assert public_api("/api/auth/login") and public_api("/api/health") and public_api("/api/location/native")
    assert public_api("/api/ics/abc/mine.ics") and public_api("/api/dofbasen/v1/obs")
    for path in ("/api/me/export", "/api/me/location/token", "/api/me/sync", "/api/meldinger", "/api/location/native/x",
                 "/api/healthz", "/api/settings/publicx"):
        assert not public_api(path), path


@pytest.fixture
async def anon(monkeypatch):
    from portal import main
    from portal.db import get_session

    async def no_session(_token):
        return None

    async def dummy_db():
        yield None

    monkeypatch.setattr(main, "session_user_id", no_session)
    main.app.dependency_overrides[get_session] = dummy_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ["/api/me/export", "/api/me/stats", "/api/me/ics", "/api/me/sync"])
async def test_anonym_stoppes_af_vagten_paa_me_underruter(anon, path):
    r = await anon.get(path)
    assert r.status_code == 401
    assert r.json()["detail"] == "Log ind for at bruge portalen"  # vagten, ikke kun rutens dependency


async def test_csrf_vaern_afviser_post_uden_header(anon):
    r = await anon.post("/api/auth/logout")
    assert r.status_code == 403
    r = await anon.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


# ---------------------------------------------------------------- S-09: «se som bruger»


def test_se_som_skjuler_ics_token_og_positionsenhed():
    from portal.api.auth import user_out
    from portal.models import User

    u = User(id=3, obserkode="ABC", navn="A", role="user", prefs={"ics_token": "hemmelig", "position_device": {"id": "x"}, "theme": "dark"})
    assert user_out(u, view_as=True)["prefs"] == {"theme": "dark"}
    assert user_out(u)["prefs"]["ics_token"] == "hemmelig"  # brugeren selv ser det stadig


# ---------------------------------------------------------------- S-02: dbadmin-login


@pytest.fixture
def dbadmin(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "dbadmin"
    if not root.is_dir():
        pytest.skip("dbadmin/ findes ikke i denne container")
    monkeypatch.setenv("DBADMIN_DATA", tempfile.mkdtemp(prefix="dbadmin-test-"))
    monkeypatch.setenv("DBADMIN_PASSWORD", "rigtigt-kodeord")
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop("app", None)
    try:
        mod = importlib.import_module("app")
    except ImportError as exc:
        pytest.skip(f"dbadmin kan ikke importeres her: {exc}")
    yield mod
    sys.modules.pop("app", None)


async def _try(mod, ip: str, password: str) -> int:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app, client=(ip, 1234)), base_url="http://test") as c:
        r = await c.post("/api/login", json={"password": password}, headers={"X-Requested-With": "fetch"})
        return r.status_code


async def test_dbadmin_samlet_loft_selv_med_skiftende_ip(dbadmin):
    codes = [await _try(dbadmin, f"10.0.{i // 250}.{i % 250}", "forkert") for i in range(dbadmin.FAILS_TOTAL)]
    assert codes == [401] * dbadmin.FAILS_TOTAL
    assert await _try(dbadmin, "10.1.2.3", "forkert") == 429  # ny "IP" hjælper ikke
    assert await _try(dbadmin, "10.1.2.4", "rigtigt-kodeord") == 429  # heller ikke det rigtige kodeord, før vinduet er gået


async def test_dbadmin_loft_pr_ip(dbadmin):
    for _ in range(dbadmin.FAILS_PER_IP):
        assert await _try(dbadmin, "10.5.5.5", "forkert") == 401
    assert await _try(dbadmin, "10.5.5.5", "forkert") == 429
