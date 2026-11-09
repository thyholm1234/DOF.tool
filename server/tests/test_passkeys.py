"""Passkeys (services/passkeys.py): api'ets egne kontroller omkring Zitadel. Kører uden database, Valkey og Zitadel –
Valkey er en ordbog, og Zitadel-klienten erstattes af små stubbe.

Zitadel kontrollerer signaturen, men ikke sidens oprindelse (afprøvet mod v4.20.2) – derfor dækker testene især
check_client_data, engangsudfordringerne og kravet om friskt login."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ORIGIN = "https://doftool.example.dk"


class KV:
    """Det udsnit af Valkey, passkeys bruger."""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, k, v, ex=None):
        self.data[k] = str(v)

    async def getdel(self, k):
        return self.data.pop(k, None)

    async def exists(self, k):
        return int(k in self.data)

    async def incr(self, k):
        self.data[k] = str(int(self.data.get(k, 0)) + 1)
        return int(self.data[k])

    async def expire(self, k, ttl):
        return True


def cd(kind="webauthn.get", challenge="abc", origin=ORIGIN, **extra) -> dict:
    raw = json.dumps({"type": kind, "challenge": challenge, "origin": origin, **extra}).encode()
    return {"id": "x", "rawId": "x", "type": "public-key",
            "response": {"clientDataJSON": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}}


@pytest.fixture
def pk(monkeypatch):
    from portal.config import get_settings
    from portal.services import passkeys

    monkeypatch.setattr(get_settings(), "public_url", ORIGIN)
    store = KV()
    monkeypatch.setattr(passkeys, "kv", lambda: store)
    passkeys._store = store
    return passkeys


class DB:
    def __init__(self, user):
        self.user = user

    async def get(self, model, key):
        return self.user if self.user and self.user.id == key else None

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.user)

    async def commit(self):
        pass


def user(**kw):
    base = dict(id=7, obserkode="ABC", navn="A B", zitadel_id="z7", blocked=False, approved=True, is_test=False, pw_hash="scrypt$x$y",
                test_pw_hash=None, email="a@b.dk",
                dof_verified_at=datetime.now(timezone.utc))
    return SimpleNamespace(**{**base, **kw})


# ---------------------------------------------------------------- clientDataJSON


def test_client_data_godkendes(pk):
    pk.check_client_data(cd(), "webauthn.get", "abc")


@pytest.mark.parametrize("cred,why", [
    (cd(origin="https://evil.example"), "anden oprindelse"),
    (cd(origin="http://doftool.example.dk"), "http i stedet for https"),
    (cd(origin="https://doftool.example.dk.evil.example"), "domæne med endelse"),
    (cd(kind="webauthn.create"), "forkert type"),
    (cd(challenge="other"), "anden udfordring"),
    (cd(crossOrigin=True), "cross-origin iframe"),
    ({"response": {"clientDataJSON": "!!"}}, "ikke base64/json"),
    ({}, "mangler"),
])
def test_client_data_afvises(pk, cred, why):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError):
        pk.check_client_data(cred, "webauthn.get", "abc")


def test_rp_id_og_origin_fra_public_url(pk):
    assert pk.rp_id() == "doftool.example.dk"
    assert pk.origin() == ORIGIN


# ---------------------------------------------------------------- login


@pytest.mark.asyncio
async def test_login_start_kraever_forbundet_passkey(pk):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        await pk.login_start(DB(user(zitadel_id=None)), "abc", "1.2.3.4")
    assert e.value.status == 404


@pytest.mark.asyncio
async def test_login_start_kraever_dof_tool_konto(pk):
    """Brugere fra før kontiene (kun obserkode) kan ikke omgå oprettelsen med en passkey fra dengang."""
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        await pk.login_start(DB(user(pw_hash=None)), "abc", "1.2.3.4")
    assert e.value.status == 409 and "konto" in str(e.value)


@pytest.mark.asyncio
async def test_login_start_kraever_ikke_ny_dofbasen_bekraeftelse(pk, monkeypatch):
    """Kontoen afløser det halvårlige DOFbasen-login: en gammel bekræftelse stopper ikke længere en passkey."""
    from portal.services import zitadel

    async def start(zid, domain):
        return {"sessionId": "s1", "challenges": {"webAuthN": {"publicKeyCredentialRequestOptions": {"publicKey": {
            "challenge": "c", "allowCredentials": [{"id": "k", "type": "public-key"}]}}}}}

    monkeypatch.setattr(zitadel, "start_session", start)
    old = user(dof_verified_at=datetime.now(timezone.utc) - timedelta(days=400))
    assert (await pk.login_start(DB(old), "abc", "1.2.3.4"))["token"]


@pytest.mark.asyncio
async def test_login_start_spaerret(pk):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        await pk.login_start(DB(user(blocked=True)), "abc", "1.2.3.4")
    assert e.value.status == 403


@pytest.mark.asyncio
async def test_login_start_graense_pr_minut(pk, monkeypatch):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        for _ in range(pk.TRIES_PER_MIN + 1):
            try:
                await pk.login_start(DB(user(zitadel_id=None)), "abc", "1.2.3.4")
            except AuthError as exc:
                if exc.status == 429:
                    raise
    assert e.value.status == 429


def _zitadel(monkeypatch, pk, factors):
    calls = {"ended": []}

    async def start_session(zid, domain):
        calls["domain"] = domain
        return {"sessionId": "s1", "challenges": {"webAuthN": {"publicKeyCredentialRequestOptions": {
            "publicKey": {"challenge": "abc", "rpId": domain, "allowCredentials": [{"id": "k", "type": "public-key"}]}}}}}

    async def check_session(sid, assertion):
        return factors

    async def end_session(sid):
        calls["ended"].append(sid)

    monkeypatch.setattr(pk.zitadel, "start_session", start_session)
    monkeypatch.setattr(pk.zitadel, "check_session", check_session)
    monkeypatch.setattr(pk.zitadel, "end_session", end_session)
    return calls


OK = {"user": {"id": "z7"}, "webAuthN": {"verifiedAt": "2026-11-09T08:00:00Z", "userVerified": True}}


@pytest.mark.asyncio
async def test_login_hele_vejen_og_kun_en_gang(pk, monkeypatch):
    from portal.services.auth import AuthError

    calls = _zitadel(monkeypatch, pk, OK)
    u = user()
    r = await pk.login_start(DB(u), "abc", "1.2.3.4")
    assert calls["domain"] == "doftool.example.dk" and r["publicKey"]["challenge"] == "abc"
    assert await pk.login_finish(DB(u), r["token"], cd()) is u
    assert calls["ended"] == ["s1"]
    with pytest.raises(AuthError):  # udfordringen er brugt
        await pk.login_finish(DB(u), r["token"], cd())


@pytest.mark.asyncio
@pytest.mark.parametrize("factors", [
    {"user": {"id": "andet"}, "webAuthN": {"verifiedAt": "x", "userVerified": True}},
    {"user": {"id": "z7"}, "webAuthN": {"verifiedAt": "x", "userVerified": False}},
    {"user": {"id": "z7"}, "webAuthN": {}},
    {},
])
async def test_login_afvises_uden_fuld_bekraeftelse(pk, monkeypatch, factors):
    from portal.services.auth import AuthError

    calls = _zitadel(monkeypatch, pk, factors)
    u = user()
    r = await pk.login_start(DB(u), "abc", "1.2.3.4")
    with pytest.raises(AuthError):
        await pk.login_finish(DB(u), r["token"], cd())
    assert calls["ended"] == ["s1"]  # Zitadel-sessionen ryddes også ved afvisning


@pytest.mark.asyncio
async def test_login_afviser_forkert_origin_foer_zitadel(pk, monkeypatch):
    from portal.services.auth import AuthError

    _zitadel(monkeypatch, pk, OK)

    async def boom(*a):
        raise AssertionError("Zitadel må ikke kaldes med en forkert oprindelse")

    monkeypatch.setattr(pk.zitadel, "check_session", boom)
    u = user()
    r = await pk.login_start(DB(u), "abc", "1.2.3.4")
    with pytest.raises(AuthError):
        await pk.login_finish(DB(u), r["token"], cd(origin="https://evil.example"))


@pytest.mark.asyncio
async def test_login_spaerret_efter_start(pk, monkeypatch):
    from portal.services.auth import AuthError

    _zitadel(monkeypatch, pk, OK)
    u = user()
    r = await pk.login_start(DB(u), "abc", "1.2.3.4")
    u.blocked = True
    with pytest.raises(AuthError) as e:
        await pk.login_finish(DB(u), r["token"], cd())
    assert e.value.status == 403


# ---------------------------------------------------------------- oprettelse


@pytest.mark.asyncio
async def test_oprettelse_kraever_friskt_login(pk):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        await pk.register_start(DB(user()), user(), "token-uden-frisk-markering")
    assert e.value.status == 403
    await pk.mark_fresh("tok")
    assert await pk.is_fresh("tok")
    assert not await pk.is_fresh(None)


@pytest.mark.asyncio
async def test_oprettelse_tjekker_type_og_udfordring(pk, monkeypatch):
    from portal.services.auth import AuthError

    verified = []

    async def list_passkeys(zid):
        return []

    async def start_passkey(zid, domain):
        return {"passkeyId": "p1", "publicKeyCredentialCreationOptions": {"publicKey": {"challenge": "reg", "rp": {"id": domain, "name": "ZITADEL"}}}}

    async def verify_passkey(zid, pid, cred, name):
        verified.append((zid, pid, name))

    for name, fn in (("list_passkeys", list_passkeys), ("start_passkey", start_passkey), ("verify_passkey", verify_passkey)):
        monkeypatch.setattr(pk.zitadel, name, fn)
    u = user()
    await pk.mark_fresh("tok")
    r = await pk.register_start(DB(u), u, "tok")
    assert r["publicKey"]["rp"]["name"] == "DOF.tool"
    with pytest.raises(AuthError):  # et login-svar kan ikke bruges til at oprette
        await pk.register_finish(u, cd(kind="webauthn.get", challenge="reg"), "x")
    assert not verified
    await pk.register_start(DB(u), u, "tok")
    await pk.register_finish(u, cd(kind="webauthn.create", challenge="reg"), "  Min telefon  ")
    assert verified == [("z7", "p1", "Min telefon")]


# ---------------------------------------------------------------- vagten, «se som bruger» og domænefilerne


def test_vagten():
    from portal.main import public_api

    assert public_api("/api/auth/passkey/start") and public_api("/api/auth/passkey/finish")
    assert not public_api("/api/me/passkeys") and not public_api("/api/me/passkeys/start")


def test_se_som_bruger_kan_ikke_aendre_passkeys():
    from portal.services import view_as

    assert view_as.blocked("POST", "/api/me/passkeys/start")
    assert view_as.blocked("POST", "/api/me/passkeys/finish")
    assert view_as.blocked("DELETE", "/api/me/passkeys/123")
    assert view_as.blocked("POST", "/api/auth/passkey/finish")


@pytest.mark.asyncio
async def test_assetlinks(monkeypatch):
    from fastapi import HTTPException

    from portal import main
    from portal.config import get_settings

    monkeypatch.setattr(get_settings(), "android_cert_sha256", "")
    with pytest.raises(HTTPException):
        await main.assetlinks()
    monkeypatch.setattr(get_settings(), "android_cert_sha256", "aa:bb")
    body = json.loads((await main.assetlinks()).body)
    assert body[0]["target"]["package_name"] == "com.chfotofilm.doftool"
    assert body[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB"]
    assert "delegate_permission/common.get_login_creds" in body[0]["relation"]
