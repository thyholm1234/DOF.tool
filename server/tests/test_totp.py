"""Totrinslogin (services/totp.py): beviset efter adgangskoden, forsøgsgrænsen, fail closed og opsætningen. Kører uden
database, Valkey og Zitadel – Valkey er en ordbog, og Zitadel-klienten erstattes af små stubbe."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class KV:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, k, v, ex=None):
        self.data[k] = str(v)

    async def get(self, k):
        return self.data.get(k)

    async def getdel(self, k):
        return self.data.pop(k, None)

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    async def exists(self, k):
        return int(k in self.data)

    async def incr(self, k):
        self.data[k] = str(int(self.data.get(k, 0)) + 1)
        return int(self.data[k])

    async def expire(self, k, ttl):
        return True


class DB:
    def __init__(self, user):
        self.user = user
        self.commits = 0

    async def get(self, model, key):
        return self.user if self.user and self.user.id == key else None

    async def commit(self):
        self.commits += 1


def user(**kw):
    base = dict(id=7, obserkode="ABC", navn="A B", zitadel_id="z7", blocked=False, approved=True, is_test=False,
                pw_hash=None, test_pw_hash=None, totp_at=None)
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def t(monkeypatch):
    from portal.services import auth, passkeys, totp

    store = KV()
    monkeypatch.setattr(totp, "kv", lambda: store)
    monkeypatch.setattr(passkeys, "kv", lambda: store)
    monkeypatch.setattr(auth, "kv", lambda: store)
    return totp


def zstub(monkeypatch, t, *, has=True, valid="123456", down=False):
    from portal.services import zitadel

    calls = {"checked": []}

    async def has_totp(zid):
        if down:
            raise zitadel.ZitadelError(503, "nede")
        return has

    async def check_totp(zid, code):
        calls["checked"].append(code)
        if down:
            raise zitadel.ZitadelError(503, "nede")
        if code != valid:
            raise zitadel.ZitadelError(400, "Invalid code")
        return {"user": {"id": zid}, "totp": {"verifiedAt": "2026-11-09T10:00:00Z"}}

    monkeypatch.setattr(t.zitadel, "has_totp", has_totp)
    monkeypatch.setattr(t.zitadel, "check_totp", check_totp)
    return calls


# ---------------------------------------------------------------- skal der kode til?


@pytest.mark.asyncio
async def test_uden_zitadel_bruger_kraeves_ingen_kode(t, monkeypatch):
    zstub(monkeypatch, t, down=True)
    assert not await t.required(DB(None), user(zitadel_id=None))


@pytest.mark.asyncio
async def test_kode_kraeves_og_flaget_spejles(t, monkeypatch):
    zstub(monkeypatch, t, has=True)
    u = user()
    assert await t.required(DB(u), u)
    assert u.totp_at is not None


@pytest.mark.asyncio
async def test_fail_closed_naar_zitadel_er_nede(t, monkeypatch):
    from portal.services.auth import AuthError

    zstub(monkeypatch, t, down=True)
    assert not await t.required(DB(None), user())  # ingen TOTP kendt: adgangskoden alene er nok
    with pytest.raises(AuthError) as e:
        await t.required(DB(None), user(totp_at="2026-01-01"))
    assert e.value.status == 503


# ---------------------------------------------------------------- login med kode


@pytest.mark.asyncio
async def test_login_med_kode_og_kun_en_gang(t, monkeypatch):
    from portal.services.auth import AuthError

    zstub(monkeypatch, t)
    u = user()
    tok = (await t.challenge(u))["totp"]
    assert await t.login(DB(u), tok, " 123 456 ", "1.2.3.4") == (u, "totp")
    with pytest.raises(AuthError):
        await t.login(DB(u), tok, "123456", "1.2.3.4")


@pytest.mark.asyncio
async def test_forkert_kode_og_graense(t, monkeypatch):
    from portal.services.auth import AuthError

    calls = zstub(monkeypatch, t)
    u = user()
    tok = (await t.challenge(u))["totp"]
    for _ in range(t.TRIES - 1):
        with pytest.raises(AuthError) as e:
            await t.login(DB(u), tok, "000000", "1.2.3.4")
        assert e.value.status == 401
    with pytest.raises(AuthError) as e:
        await t.login(DB(u), tok, "000000", "1.2.3.4")
    assert "for mange" in str(e.value).lower()
    with pytest.raises(AuthError):  # beviset er kasseret – heller ikke den rigtige kode virker
        await t.login(DB(u), tok, "123456", "1.2.3.4")
    assert calls["checked"].count("123456") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "abcd-efghi", "ab!cd-efghi"])
async def test_ugyldigt_format_kalder_ikke_zitadel(t, monkeypatch, code):
    from portal.services.auth import AuthError

    calls = zstub(monkeypatch, t)
    u = user()
    tok = (await t.challenge(u))["totp"]
    with pytest.raises(AuthError):
        await t.login(DB(u), tok, code, "1.2.3.4")
    assert not calls["checked"]


@pytest.mark.asyncio
async def test_spaerret_efter_kode(t, monkeypatch):
    from portal.services.auth import AuthError

    zstub(monkeypatch, t)
    u = user()
    tok = (await t.challenge(u))["totp"]
    u.blocked = True
    with pytest.raises(AuthError) as e:
        await t.login(DB(u), tok, "123456", "1.2.3.4")
    assert e.value.status == 403


@pytest.mark.asyncio
async def test_faktorer_skal_passe(t, monkeypatch):
    from portal.services.auth import AuthError

    zstub(monkeypatch, t)

    async def wrong_user(zid, code):
        return {"user": {"id": "andet"}, "totp": {"verifiedAt": "x"}}

    monkeypatch.setattr(t.zitadel, "check_totp", wrong_user)
    u = user()
    tok = (await t.challenge(u))["totp"]
    with pytest.raises(AuthError):
        await t.login(DB(u), tok, "123456", "1.2.3.4")


# ---------------------------------------------------------------- opsætning


def test_otpauth_uri_bruger_dof_som_udsteder(t):
    uri = t.otpauth_uri("ABCD", "8000ABC")
    assert uri.startswith("otpauth://totp/DOF:8000ABC?") and "secret=ABCD" in uri and "issuer=DOF" in uri


def test_qr_er_svg(t):
    assert t.qr_svg("otpauth://totp/DOF:X?secret=AB").startswith("<svg")


@pytest.mark.asyncio
async def test_opsaetning_kraever_friskt_login(t):
    from portal.services.auth import AuthError

    with pytest.raises(AuthError) as e:
        await t.setup_start(DB(user()), user(), "tok-uden-frisk")
    assert e.value.status == 403


@pytest.mark.asyncio
async def test_opsaetning_hele_vejen(t, monkeypatch):
    from portal.services import passkeys

    verified = []

    async def start_totp(zid):
        return "SECRETX"

    async def verify_totp(zid, code):
        verified.append((zid, code))

    async def generate(zid):
        return ["aaaaa-bbbbb", "ccccc-ddddd"]

    monkeypatch.setattr(t.zitadel, "start_totp", start_totp)
    monkeypatch.setattr(t.zitadel, "verify_totp", verify_totp)
    monkeypatch.setattr(t.zitadel, "generate_recovery_codes", generate)
    u = user()
    await passkeys.mark_fresh("tok")
    r = await t.setup_start(DB(u), u, "tok")
    assert r["secret"] == "SECRETX" and "secret=SECRETX" in r["uri"] and r["qr"].startswith("<svg")
    r = await t.setup_finish(DB(u), u, "654321", "1.2.3.4")
    assert verified == [("z7", "654321")] and u.totp_at is not None
    assert r["recovery_codes"] == ["aaaaa-bbbbb", "ccccc-ddddd"]


@pytest.mark.asyncio
async def test_slaa_fra_kraever_adgangskode(t, monkeypatch):
    from portal.services import auth
    from portal.services.auth import AuthError

    removed = []

    async def remove_totp(zid):
        removed.append(zid)

    async def remove_rc(zid):
        removed.append("rc:" + zid)

    monkeypatch.setattr(t.zitadel, "remove_totp", remove_totp)
    monkeypatch.setattr(t.zitadel, "remove_recovery_codes", remove_rc)
    u = user(pw_hash=auth.hash_password("rigtig-adgangskode"), totp_at="2026-01-01")
    with pytest.raises(AuthError):
        await t.disable(DB(u), u, "forkert", "1.2.3.4")
    assert not removed
    await t.disable(DB(u), u, "rigtig-adgangskode", "1.2.3.4")
    assert removed == ["z7", "rc:z7"] and u.totp_at is None


# ---------------------------------------------------------------- vagten og «se som bruger»


def test_vagten():
    from portal.main import public_api

    assert public_api("/api/auth/totp")
    assert not public_api("/api/me/totp") and not public_api("/api/me/totp/start")


def test_se_som_bruger_kan_ikke_aendre_totp():
    from portal.services import view_as

    for path in ("/api/me/totp/start", "/api/me/totp/finish", "/api/me/totp/off", "/api/me/totp/recovery", "/api/auth/totp"):
        assert view_as.blocked("POST", path)


# ---------------------------------------------------------------- genoprettelseskoder


@pytest.mark.parametrize("given,kind,norm", [("123456", "totp", "123456"), ("12 34 56", "totp", "123456"),
                                             ("ab12c-3de45", "recovery", "ab12c-3de45"), (" AB12C3DE45 ", "recovery", "ab12c-3de45")])
def test_parse(t, given, kind, norm):
    assert t.parse(given) == (kind, norm)


@pytest.mark.asyncio
async def test_login_med_genoprettelseskode(t, monkeypatch):
    from portal.services.auth import AuthError

    calls = zstub(monkeypatch, t)
    used = []

    async def check_rc(zid, code):
        if code in used or code != "ab12c-3de45":
            raise t.zitadel.ZitadelError(400, "Invalid recovery code provided")
        used.append(code)
        return {"user": {"id": zid}, "recoveryCode": {"verifiedAt": "x"}}

    monkeypatch.setattr(t.zitadel, "check_recovery_code", check_rc)
    u = user()
    tok = (await t.challenge(u))["totp"]
    assert await t.login(DB(u), tok, "AB12C3DE45", "1.2.3.4") == (u, "recovery")
    assert not calls["checked"]  # ikke sendt som TOTP-kode
    tok = (await t.challenge(u))["totp"]
    with pytest.raises(AuthError) as e:  # koden virker kun én gang
        await t.login(DB(u), tok, "ab12c-3de45", "1.2.3.4")
    assert e.value.status == 401


@pytest.mark.asyncio
async def test_genoprettelseskode_kraever_rigtig_faktor(t, monkeypatch):
    """Et svar med en TOTP-faktor tæller ikke som en kontrolleret genoprettelseskode (og omvendt)."""
    from portal.services.auth import AuthError

    zstub(monkeypatch, t)

    async def check_rc(zid, code):
        return {"user": {"id": zid}, "totp": {"verifiedAt": "x"}}

    monkeypatch.setattr(t.zitadel, "check_recovery_code", check_rc)
    u = user()
    tok = (await t.challenge(u))["totp"]
    with pytest.raises(AuthError):
        await t.login(DB(u), tok, "ab12c-3de45", "1.2.3.4")


@pytest.mark.asyncio
async def test_nye_koder_kraever_adgangskode_og_totp(t, monkeypatch):
    from portal.services import auth
    from portal.services.auth import AuthError

    state = {"has": False}

    async def has_totp(zid):
        return state["has"]

    async def generate(zid):
        return ["new01-codes"]

    monkeypatch.setattr(t.zitadel, "has_totp", has_totp)
    monkeypatch.setattr(t.zitadel, "generate_recovery_codes", generate)
    u = user(pw_hash=auth.hash_password("rigtig-adgangskode"))
    with pytest.raises(AuthError):
        await t.new_recovery_codes(DB(u), u, "forkert", "1.2.3.4")
    with pytest.raises(AuthError):
        await t.new_recovery_codes(DB(u), u, "rigtig-adgangskode", "1.2.3.4")
    state["has"] = True
    assert (await t.new_recovery_codes(DB(u), u, "rigtig-adgangskode", "1.2.3.4"))["recovery_codes"] == ["new01-codes"]
