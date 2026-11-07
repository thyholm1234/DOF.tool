"""Baggrundspositionen: afrunding, hashede positionstokens (også gamle tokens flyttes over), rate limit, ingen
koordinater i loggen, og share_location kan ikke sættes uden om positions-endpointene."""

import logging
from types import SimpleNamespace

import pytest

from portal.api import auth as auth_api
from portal.api import position
from portal.services import position_privacy as pp


class FakeKV:
    def __init__(self):
        self.d, self.ttls = {}, {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k] = str(v)
        self.ttls[k] = ex
        return True

    async def delete(self, *ks):
        return sum(self.d.pop(k, None) is not None for k in ks)

    async def exists(self, *ks):
        return sum(k in self.d for k in ks)

    async def ttl(self, k):
        return self.ttls.get(k) or -1

    async def incr(self, k):
        self.d[k] = str(int(self.d.get(k, 0)) + 1)
        return int(self.d[k])

    async def expire(self, k, s):
        self.ttls[k] = s


@pytest.fixture
def kv(monkeypatch):
    r = FakeKV()
    monkeypatch.setattr(position, "kv", lambda: r)
    return r


def test_position_is_coarsened():
    assert pp.coarse(55.676098, 12.568337) == (55.676, 12.568)


async def test_token_is_stored_only_as_hash(kv):
    token = await position._new_token(7, "device-abc")
    assert all(token not in k and token not in v for k, v in kv.d.items())
    assert kv.d[pp.token_key(token)] == "7:device-abc"
    assert await position._token_owner(token) == "7:device-abc"
    assert await position._token_owner("forkert") is None


async def test_new_token_revokes_old(kv):
    old = await position._new_token(7, "device-abc")
    new = await position._new_token(7, "device-xyz")
    assert await position._token_owner(old) is None
    assert await position._token_owner(new) == "7:device-xyz"
    await position._revoke_token(7)
    assert await position._token_owner(new) is None and not kv.d


async def test_legacy_plain_token_is_migrated(kv):
    kv.d["loctok:gammelt"] = "7:device-abc"
    kv.d["loctok:user:7"] = "gammelt"
    assert await position._token_owner("gammelt") == "7:device-abc"
    assert "loctok:gammelt" not in kv.d and kv.d["loctok:user:7"] == pp.token_key("gammelt")
    await position._revoke_token(7)
    assert not kv.d


async def test_legacy_pointer_is_revoked(kv):
    kv.d["loctok:gammelt"] = "7:device-abc"
    kv.d["loctok:user:7"] = "gammelt"
    await position._revoke_token(7)
    assert not kv.d


async def test_throttle(kv):
    assert await position._throttle(7)
    assert not await position._throttle(7)
    assert await position._throttle(8)


def test_access_log_has_no_coordinates():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/feed?scope=near%3A55.676%2C12.568%2C25&level=SU", "1.1", 200), None)
    pp.RedactPositions().filter(rec)
    line = rec.getMessage()
    assert "55.676" not in line and "12.568" not in line and "near%3A[position]" in line
    assert pp.redact("/api/feed?scope=near:-55.6,12.5,5") == "/api/feed?scope=near:[position],5"


def test_share_location_cannot_be_patched():
    assert "share_location" not in auth_api.ALLOWED_PREFS and "position_device" not in auth_api.ALLOWED_PREFS


def test_view_as_hides_position_device(monkeypatch):
    user = SimpleNamespace(id=1, obserkode="X", navn="X", role="user", history_synced_at=None, permissions=None,
                           prefs={"share_location": True, "position_device": {"id": "d", "name": "Pixel"}})
    monkeypatch.setattr(auth_api.auth, "modules", lambda u: set())
    assert "position_device" in auth_api.user_out(user)["prefs"]
    assert "position_device" not in auth_api.user_out(user, view_as=True)["prefs"]


def test_viewing_as():
    assert not pp.viewing_as(None)
    assert not pp.viewing_as(SimpleNamespace(state=SimpleNamespace()))
    assert pp.viewing_as(SimpleNamespace(state=SimpleNamespace(view_as={"user_id": 1})))
