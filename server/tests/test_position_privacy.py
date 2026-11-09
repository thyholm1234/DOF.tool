"""Baggrundspositionen: afrunding, hashede positions- og sessionstokens (også gamle tokens flyttes over), tokenet
tilbagekaldes ved logout på telefonen, rate limit, ingen koordinater i loggen, og share_location kan ikke sættes uden
om positions-endpointene."""

import logging
from types import SimpleNamespace

import pytest

from portal.api import auth as auth_api
from portal.api import position
from portal.services import auth as auth_service
from portal.services import position_privacy as pp
from portal.services import view_as


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

    async def sadd(self, k, v):
        self.d.setdefault(k, set()).add(v)

    async def srem(self, k, v):
        self.d.get(k, set()).discard(v)


@pytest.fixture
def kv(monkeypatch):
    r = FakeKV()
    for mod in (position, pp, auth_service):
        monkeypatch.setattr(mod, "kv", lambda: r)
    return r


def test_position_is_coarsened():
    assert pp.coarse(55.676098, 12.568337) == (55.676, 12.568)


async def test_token_is_stored_only_as_hash(kv):
    token = await pp.new_token(7, "device-abc", None)
    assert all(token not in k and token not in v for k, v in kv.d.items())
    assert kv.d[pp.token_key(token)] == "7:device-abc"
    assert await pp.token_owner(token) == "7:device-abc"
    assert await pp.token_owner("forkert") is None


async def test_new_token_revokes_old(kv):
    old = await pp.new_token(7, "device-abc", None)
    new = await pp.new_token(7, "device-xyz", None)
    assert await pp.token_owner(old) is None
    assert await pp.token_owner(new) == "7:device-xyz"
    await pp.revoke_token(7)
    assert await pp.token_owner(new) is None and not kv.d


async def test_legacy_plain_token_is_not_accepted(kv):
    """Overgangsreglen for tokens fra før hashingen er fjernet (runde 4, U-07): der findes ingen, og reglen slog brugerens
    input op som en rå Valkey-nøgle."""
    kv.d["loctok:gammelt-token-fra-foer-hashingen"] = "7:device-abc"
    assert await pp.token_owner("gammelt-token-fra-foer-hashingen") is None
    assert "loctok:gammelt-token-fra-foer-hashingen" in kv.d  # opslaget rører ikke nøglen


async def test_legacy_pointer_is_revoked(kv):
    kv.d["loctok:gammelt"] = "7:device-abc"
    kv.d["loctok:user:7"] = "gammelt"
    await pp.revoke_token(7)
    assert not kv.d


async def test_logout_on_the_sharing_phone_revokes_token(kv):
    token = await pp.new_token(7, "device-abc", "telefonens-session")
    await pp.revoke_for_session("browserens-session")  # logout et andet sted: telefonen deler stadig
    assert await pp.token_owner(token) == "7:device-abc"
    assert all("telefonens-session" not in k for k in kv.d)
    await pp.revoke_for_session("telefonens-session")
    assert await pp.token_owner(token) is None and not kv.d


async def test_old_session_cannot_revoke_newer_token(kv):
    await pp.new_token(7, "device-abc", "gammel-session")
    token = await pp.new_token(7, "device-abc", "ny-session")
    await pp.revoke_for_session("gammel-session")
    assert await pp.token_owner(token) == "7:device-abc"


async def test_session_is_stored_only_as_hash(kv, monkeypatch):
    monkeypatch.setattr(auth_service, "get_settings", lambda: SimpleNamespace(session_days=30))
    token = await auth_service.create_session(SimpleNamespace(id=7))
    assert all(token not in k and token not in str(v) for k, v in kv.d.items())
    assert await auth_service.session_user_id(token) == "7"
    assert await auth_service.destroy_session(token) == "7"
    assert await auth_service.session_user_id(token) is None


async def test_legacy_session_is_not_accepted(kv):
    # Sessioner fra før hashingen lå i klartekst (også i Valkey-kopier) – de godtages ikke længere, men slettes ved
    # opstart af auth.purge_legacy_sessions (sikkerhedsgennemgangen 2026-11-08, S-01; se også test_security.py)
    kv.d["sess:gammel"], kv.ttls["sess:gammel"] = "7", 1000
    kv.d["usess:7"] = {"gammel"}
    assert await auth_service.session_user_id("gammel") is None
    assert auth_service.session_key("gammel") not in kv.d


def test_view_as_state_key_is_hashed():
    assert "hemmelig" not in view_as.key_for("hemmelig")


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
                           email="x@example.dk", pw_hash="scrypt$a$b",
                           prefs={"share_location": True, "position_device": {"id": "d", "name": "Pixel"}})
    monkeypatch.setattr(auth_api.auth, "modules", lambda u: set())
    assert "position_device" in auth_api.user_out(user)["prefs"]
    assert "position_device" not in auth_api.user_out(user, view_as=True)["prefs"]
    assert auth_api.user_out(user)["email"] == "x@example.dk" and auth_api.user_out(user, view_as=True)["email"] is None


def test_viewing_as():
    assert not pp.viewing_as(None)
    assert not pp.viewing_as(SimpleNamespace(state=SimpleNamespace()))
    assert pp.viewing_as(SimpleNamespace(state=SimpleNamespace(view_as={"user_id": 1})))
