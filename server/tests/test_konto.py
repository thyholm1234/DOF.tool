"""DOF.tool-konti (services/konto.py): login med egen adgangskode, binding til obserkoden via ét DOFbasen-login, ny
adgangskode og den engangs-udlogning af alle, der var logget ind med obserkoden. Kører uden database og Valkey."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_security import FakeKV


class KV(FakeKV):
    async def getdel(self, k):
        return self.data.pop(k, None)


@pytest.fixture
def kv(monkeypatch):
    from portal.services import account, auth, konto, position_privacy

    store = KV()
    for mod in (auth, account, konto, position_privacy):
        monkeypatch.setattr(mod, "kv", lambda: store)
    return store


@pytest.fixture
def dof(monkeypatch):
    """DOFbasen-kontrollen: godkender kun adgangskoden «dof-hemmelig». Kaldene tælles."""
    from portal.services import auth

    calls = []

    async def verify(kode, password):
        calls.append(kode)
        if password != "dof-hemmelig":
            raise auth.AuthError("Forkert obserkode eller DOFbasen-adgangskode (tjekket hos DOFbasen)")

    monkeypatch.setattr(auth, "verify_dofbasen", verify)
    return calls


@pytest.fixture
def approval(monkeypatch):
    from portal.services import auth

    state = {"required": False, "notified": []}

    async def required(db):
        return state["required"]

    async def notify(db, user):
        state["notified"].append(user.obserkode)

    monkeypatch.setattr(auth, "approval_required", required)
    monkeypatch.setattr(auth, "notify_pending", notify)
    return state


class DB:
    """Én bruger (eller ingen). Forespørgslerne skelnes på deres tekst."""

    def __init__(self, user=None, email_owner=None, observer="Anna Andersen"):
        self.user, self.email_owner, self.observer = user, email_owner, observer
        self.added, self.commits, self.marker = [], 0, False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "observers" in sql:
            return SimpleNamespace(scalar=lambda: self.observer)
        if "INSERT INTO settings" in sql:
            first = not self.marker
            self.marker = True
            return SimpleNamespace(first=lambda: ("x",) if first else None)
        if "DELETE FROM settings" in sql:
            self.marker = False
            return SimpleNamespace()
        if sql.split("FROM")[0].strip() == "SELECT users.id":  # opslag af e-mailens ejer
            return SimpleNamespace(scalar=lambda: self.email_owner)
        user = self.user
        if "lower(users.email)" in sql and user is not None and user.email is None:  # opslag på e-mail
            user = None
        return SimpleNamespace(scalar_one_or_none=lambda: user)

    def add(self, obj):
        self.added.append(obj)
        obj.id = 99
        self.user = obj

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass


def user(**kw):
    from portal.services import auth

    base = dict(id=7, obserkode="8000ABC", navn="Anna Andersen", email="anna@example.dk", pw_hash=auth.hash_password("rigtig-kode-1"),
                is_test=False, test_pw_hash=None, blocked=False, approved=True, account_at=None, dof_verified_at=None,
                history_synced_at=None, prefs={})
    return SimpleNamespace(**{**base, **kw})


# ---------------------------------------------------------------- login


async def test_login_med_obserkode_og_med_email(kv):
    from portal.services import konto

    u = user()
    assert await konto.login(DB(u), "8000abc", "rigtig-kode-1", "1.1.1.1") is u
    assert await konto.login(DB(u), "Anna@Example.dk", "rigtig-kode-1", "1.1.1.2") is u


async def test_login_forkert_kode_og_ukendt_bruger_giver_samme_svar(kv):
    from portal.services import auth, konto

    with pytest.raises(auth.AuthError) as wrong:
        await konto.login(DB(user()), "8000ABC", "forkert-kode", "1.1.1.1")
    with pytest.raises(auth.AuthError) as unknown:
        await konto.login(DB(None), "ingen@example.dk", "forkert-kode", "1.1.1.1")
    assert wrong.value.status == unknown.value.status == 401
    assert str(wrong.value) == str(unknown.value) == konto.BAD_LOGIN


async def test_login_uden_konto_sender_til_oprettelsen(kv):
    """Brugere fra før kontiene (kun obserkode) kan ikke logge ind – heller ikke med deres DOFbasen-adgangskode."""
    from portal.services import auth, konto

    with pytest.raises(auth.AuthError) as exc:
        await konto.login(DB(user(pw_hash=None, email=None)), "8000ABC", "dof-hemmelig", "1.1.1.1")
    assert exc.value.status == 409


async def test_login_testbruger_med_lokal_kode(kv):
    from portal.services import auth, konto

    t = user(obserkode="TEST0001", is_test=True, pw_hash=None, email=None, test_pw_hash=auth.hash_password("testkode12"))
    assert await konto.login(DB(t), "TEST0001", "testkode12", "1.1.1.1") is t


async def test_login_spaerret_og_afventende(kv):
    from portal.services import auth, konto

    with pytest.raises(auth.AuthError) as blocked:
        await konto.login(DB(user(blocked=True)), "8000ABC", "rigtig-kode-1", "1.1.1.1")
    with pytest.raises(auth.AuthError) as pending:
        await konto.login(DB(user(approved=False)), "8000ABC", "rigtig-kode-1", "1.1.1.2")
    assert blocked.value.status == pending.value.status == 403
    assert "afventer" in str(pending.value)


async def test_login_loft(kv):
    from portal.services import auth, konto

    for _ in range(auth.LOGIN_PER_MIN):
        with pytest.raises(auth.AuthError):
            await konto.login(DB(user()), "8000ABC", "forkert-kode", "1.1.1.1")
    with pytest.raises(auth.AuthError) as exc:
        await konto.login(DB(user()), "8000ABC", "rigtig-kode-1", "1.1.1.1")
    assert exc.value.status == 429


# ---------------------------------------------------------------- trin 1: bekræft hos DOFbasen


async def test_bekraeft_giver_engangsbevis_kun_som_hash(kv, dof):
    from portal.services import konto

    r = await konto.verify(DB(None), "8000abc", "dof-hemmelig", "1.1.1.1")
    assert r["obserkode"] == "8000ABC" and r["navn"] == "Anna Andersen" and r["has_account"] is False
    assert dof == ["8000ABC"]
    assert not any(r["token"] in k for k in kv.data)  # tokenet står ikke råt i Valkey
    assert any(k.startswith("konto:bind:") for k in kv.data)


async def test_bekraeft_eksisterende_konto_viser_maskeret_email(kv, dof):
    from portal.services import konto

    r = await konto.verify(DB(user()), "8000ABC", "dof-hemmelig", "1.1.1.1")
    assert r["has_account"] is True and r["email"] == "a•••@example.dk"


async def test_bekraeft_afvist_hos_dofbasen_giver_intet_bevis(kv, dof):
    from portal.services import auth, konto

    with pytest.raises(auth.AuthError):
        await konto.verify(DB(None), "8000ABC", "forkert", "1.1.1.1")
    assert not any(k.startswith("konto:bind:") for k in kv.data)


async def test_bekraeft_testbruger_afvises_uden_dofbasen_kald(kv, dof):
    from portal.services import auth, konto

    with pytest.raises(auth.AuthError) as exc:
        await konto.verify(DB(user(obserkode="TEST0001", is_test=True)), "TEST0001", "dof-hemmelig", "1.1.1.1")
    assert exc.value.status == 400 and dof == []


# ---------------------------------------------------------------- trin 2: opret / ny adgangskode


async def test_opret_ny_bruger_og_beviset_bruges_kun_en_gang(kv, dof, approval):
    from portal.services import auth, konto

    db = DB(None)
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    u, new = await konto.complete(db, token, "en-god-kode-1", "Anna@Example.dk", "Anna A.")
    assert new and u.obserkode == "8000ABC" and u.email == "anna@example.dk" and u.navn == "Anna A."
    assert auth.check_password(u.pw_hash, "en-god-kode-1") and u.account_at and u.dof_verified_at
    with pytest.raises(auth.AuthError) as again:
        await konto.complete(db, token, "en-god-kode-2", "anna@example.dk")
    assert again.value.status == 400 and "udløbet" in str(again.value)


async def test_svag_kode_bruger_ikke_beviset(kv, dof, approval):
    """En afvist adgangskode må ikke kræve et nyt DOFbasen-login."""
    from portal.services import auth, konto

    db = DB(None)
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    for bad in ("kort", "8000abc", "aaaaaaaaaaaa", "1234567890"):
        with pytest.raises(auth.AuthError):
            await konto.complete(db, token, bad, "anna@example.dk")
    with pytest.raises(auth.AuthError):
        await konto.complete(db, token, "en-god-kode-1", "ikke-en-mail")
    u, _ = await konto.complete(db, token, "en-god-kode-1", "anna@example.dk")
    assert u.pw_hash


async def test_eksisterende_bruger_bindes_og_logges_ud(kv, dof, approval):
    """En bruger fra før kontiene beholder sin række (og dermed alle data) og logges ud overalt."""
    from portal.services import auth, konto

    old = user(pw_hash=None, email=None)
    db = DB(old)
    stale = await auth.create_session(old)
    kv.data["loctok:user:7"] = "loctok:h:abc"
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    u, new = await konto.complete(db, token, "en-god-kode-1", "anna@example.dk")
    assert u is old and not new and u.id == 7 and u.email == "anna@example.dk" and u.account_at
    assert await auth.session_user_id(stale) is None
    assert "loctok:user:7" not in kv.data and db.added == []


async def test_glemt_kode_kraever_ikke_email(kv, dof, approval):
    from portal.services import auth, konto

    u = user()
    db = DB(u)
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    await konto.complete(db, token, "helt-ny-kode-9")
    assert auth.check_password(u.pw_hash, "helt-ny-kode-9") and u.email == "anna@example.dk"


async def test_email_i_brug_afvises(kv, dof, approval):
    from portal.services import auth, konto

    db = DB(None, email_owner=42)
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    with pytest.raises(auth.AuthError) as exc:
        await konto.complete(db, token, "en-god-kode-1", "optaget@example.dk")
    assert exc.value.status == 409


async def test_ny_bruger_afventer_godkendelse(kv, dof, approval):
    from portal.services import auth, konto

    approval["required"] = True
    db = DB(None)
    token = (await konto.verify(db, "8000ABC", "dof-hemmelig", "1.1.1.1"))["token"]
    with pytest.raises(auth.AuthError) as exc:
        await konto.complete(db, token, "en-god-kode-1", "anna@example.dk")
    assert exc.value.status == 403 and approval["notified"] == ["8000ABC"]
    assert db.user.pw_hash and db.user.approved is False  # kontoen findes – login venter på godkendelsen


async def test_skift_kode_kraever_nuvaerende(kv):
    from portal.services import auth, konto

    u = user()
    with pytest.raises(auth.AuthError) as exc:
        await konto.change_password(DB(u), u, "forkert", "helt-ny-kode-9", "1.1.1.1")
    assert exc.value.status == 403
    stale = await auth.create_session(u)
    await konto.change_password(DB(u), u, "rigtig-kode-1", "helt-ny-kode-9", "1.1.1.1")
    assert auth.check_password(u.pw_hash, "helt-ny-kode-9") and await auth.session_user_id(stale) is None


# ---------------------------------------------------------------- alle logges ud én gang


async def test_alle_logges_ud_en_gang(kv):
    from portal.services import auth, konto

    t1 = await auth.create_session(SimpleNamespace(id=1))
    await auth.create_session(SimpleNamespace(id=2))
    kv.data["loctok:h:abc"] = "1:dev"
    kv.data["pk:login:x"] = "{}"
    kv.data["feed:cache"] = "behold"
    db = DB(None)
    assert await konto.logout_everyone_once(db) > 0
    assert await auth.session_user_id(t1) is None
    assert not any(k.startswith(("sess:", "usess:", "loctok:", "pk:")) for k in kv.data)
    assert kv.data["feed:cache"] == "behold"
    # nye sessioner efter overgangen overlever en genstart
    t3 = await auth.create_session(SimpleNamespace(id=3))
    assert await konto.logout_everyone_once(db) == 0
    assert await auth.session_user_id(t3) == "3"


# ---------------------------------------------------------------- regler og ruter


def test_email_og_maskering():
    from portal.services import auth, konto

    assert konto.normalize_email("  Anna@Example.DK ") == "anna@example.dk"
    for bad in ("", "anna", "anna@", "@example.dk", "a b@example.dk", "anna@example"):
        with pytest.raises(auth.AuthError):
            konto.normalize_email(bad)
    assert konto.mask_email("christian@gmail.com") == "c•••@gmail.com"
    assert konto.mask_email(None) is None


def test_vagten():
    from portal.main import public_api

    assert public_api("/api/auth/konto/bekraeft") and public_api("/api/auth/konto/opret")
    assert not public_api("/api/me/password") and not public_api("/api/me/email")


def test_hashen_er_hemmelig_i_eksport():
    from portal.services import account

    assert "pw_hash" in account.SECRET_COLUMNS
