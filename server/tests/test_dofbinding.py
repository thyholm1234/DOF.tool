"""Forbindelsen til DOFbasen (services/dofbinding.py): fjern og bind, de flyttede data og Zitadel-brugernavnet. Kører
uden database, Valkey og Zitadel."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_security import FakeKV


class DB:
    def __init__(self, user, taken=(), known=()):
        self.user, self.taken, self.known = user, set(taken), set(known)
        self.sql: list[tuple[str, dict]] = []
        self.commits = self.rollbacks = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM observers" in sql:
            return SimpleNamespace(first=lambda: (1,) if params["k"] in self.known else None)
        if sql.lstrip().upper().startswith(("UPDATE", "DELETE")):
            self.sql.append((sql, params))
            return SimpleNamespace()
        code = stmt.compile().params.get("obserkode_1")  # SELECT users.id WHERE obserkode = …
        return SimpleNamespace(first=lambda: (5,) if code in self.taken else None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def user(**kw):
    from portal.services import auth

    base = dict(id=42, obserkode="8220CVH", navn="Anna", role="user", is_test=False, zitadel_id="z42",
                pw_hash=auth.hash_password("rigtig-kode-1"), dof_verified_at="2026-01-01", history_synced_at="2026-01-01")
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def env(monkeypatch):
    from portal.services import auth, dofbinding

    store = FakeKV()
    monkeypatch.setattr(auth, "kv", lambda: store)
    calls = {"renamed": [], "worker": [], "dof": []}

    async def rename(zid, name):
        calls["renamed"].append((zid, name))

    async def worker(cmd):
        calls["worker"].append(cmd)

    async def bump(*a):
        pass

    async def verify(kode, pw):
        calls["dof"].append(kode)
        if pw != "dof-hemmelig":
            raise auth.AuthError("Forkert obserkode eller DOFbasen-adgangskode")

    async def name(db, kode):
        return "Anna Andersen"

    monkeypatch.setattr(dofbinding.zitadel, "rename_user", rename)
    monkeypatch.setattr(dofbinding, "worker_command", worker)
    monkeypatch.setattr(dofbinding, "bump", bump)
    monkeypatch.setattr(auth, "verify_dofbasen", verify)
    monkeypatch.setattr(auth, "observer_name", name)
    return calls


def test_koder():
    from portal.services import dofbinding

    assert dofbinding.local_code(42) == "DT000042"
    assert dofbinding.is_local("DT000042") and dofbinding.is_local("DT1234567")
    for real in ("8220CVH", "DOFKBH", "DT", "DTABC123", "0BKR"):
        assert not dofbinding.is_local(real)
    assert not dofbinding.is_bound(user(obserkode="DT000042")) and not dofbinding.is_bound(user(is_test=True, obserkode="TEST0001"))
    assert dofbinding.is_bound(user())


@pytest.mark.asyncio
async def test_fjern_forbindelse(env):
    from portal.services import dofbinding

    u = user()
    db = DB(u)
    assert await dofbinding.unbind(db, u, "rigtig-kode-1", "1.1.1.1") == "8220CVH"
    assert u.obserkode == "DT000042" and u.dof_verified_at is None
    assert env["renamed"] == [("z42", "DT000042")] and env["worker"] == ["doftool_sync"]
    tables = [s.split()[1] if s.startswith("UPDATE") else "delete" for s, _ in db.sql]
    assert tables == ["field_trips", "field_reports", "kl_kryds", "nf_kryds", "delete", "list_group_members"]
    assert all(p["old"] == "8220CVH" and p["new"] == "DT000042" for _, p in db.sql)
    assert all("user_id = :uid" in s for s, _ in db.sql[:2])  # kun egne indtastninger


@pytest.mark.asyncio
@pytest.mark.parametrize("kw,pw,status", [
    ({}, "forkert", 401),
    ({"role": "superadmin"}, "rigtig-kode-1", 400),
    ({"obserkode": "DT000042"}, "rigtig-kode-1", 400),
    ({"is_test": True, "obserkode": "TEST0001"}, "rigtig-kode-1", 400),
])
async def test_fjern_afvises(env, kw, pw, status):
    from portal.services import dofbinding
    from portal.services.auth import AuthError

    u = user(**kw)
    with pytest.raises(AuthError) as e:
        await dofbinding.unbind(DB(u), u, pw, "1.1.1.1")
    assert e.value.status == status and not env["renamed"] and u.obserkode == kw.get("obserkode", "8220CVH")


@pytest.mark.asyncio
async def test_lokal_kode_optaget_i_observatoerregistret(env):
    from portal.services import dofbinding
    from portal.services.auth import AuthError

    u = user()
    with pytest.raises(AuthError) as e:
        await dofbinding.unbind(DB(u, known={"DT000042"}), u, "rigtig-kode-1", "1.1.1.1")
    assert e.value.status == 409 and u.obserkode == "8220CVH"


@pytest.mark.asyncio
async def test_bind(env):
    from portal.services import dofbinding

    u = user(obserkode="DT000042", dof_verified_at=None, navn="DT000042")
    db = DB(u)
    await dofbinding.bind(db, u, "8220cvh", "dof-hemmelig", "1.1.1.1")
    assert u.obserkode == "8220CVH" and u.dof_verified_at and u.history_synced_at is None and u.navn == "Anna Andersen"
    assert env["dof"] == ["8220CVH"] and env["renamed"] == [("z42", "8220CVH")]
    assert all(p["old"] == "DT000042" and p["new"] == "8220CVH" for _, p in db.sql)


@pytest.mark.asyncio
async def test_bind_kun_hvis_koden_er_ledig(env):
    from portal.services import dofbinding
    from portal.services.auth import AuthError

    u = user(obserkode="DT000042")
    with pytest.raises(AuthError) as e:
        await dofbinding.bind(DB(u, taken={"8220CVH"}), u, "8220CVH", "dof-hemmelig", "1.1.1.1")
    assert e.value.status == 409 and not env["dof"] and u.obserkode == "DT000042"


@pytest.mark.asyncio
@pytest.mark.parametrize("kode,pw,bound", [("8220CVH", "forkert", False), ("DT000043", "dof-hemmelig", False),
                                           ("8220CVH", "dof-hemmelig", True)])
async def test_bind_afvises(env, kode, pw, bound):
    from portal.services import dofbinding
    from portal.services.auth import AuthError

    u = user() if bound else user(obserkode="DT000042")
    before = u.obserkode
    with pytest.raises(AuthError):
        await dofbinding.bind(DB(u), u, kode, pw, "1.1.1.1")
    assert u.obserkode == before and not env["renamed"]


@pytest.mark.asyncio
async def test_zitadel_omdoebes_tilbage_ved_fejl(env, monkeypatch):
    from portal.services import dofbinding

    u = user()
    db = DB(u)

    async def boom(stmt, params=None):
        if str(stmt).startswith("UPDATE kl_kryds"):
            raise RuntimeError("db nede")
        return await DB.execute(db, stmt, params)

    monkeypatch.setattr(db, "execute", boom)
    with pytest.raises(RuntimeError):
        await dofbinding.unbind(db, u, "rigtig-kode-1", "1.1.1.1")
    assert env["renamed"] == [("z42", "DT000042"), ("z42", "8220CVH")] and u.obserkode == "8220CVH" and db.rollbacks == 1


def test_se_som_bruger_og_vagten():
    from portal.main import public_api
    from portal.services import view_as

    assert view_as.blocked("POST", "/api/me/dofbasen/unbind") and view_as.blocked("POST", "/api/me/dofbasen/bind")
    assert not public_api("/api/me/dofbasen/unbind") and public_api("/api/auth/konto/opret-lokal")


# ---------------------------------------------------------------- superadmin bundet til e-mailen


class SuperDB:
    """users-tabellen som en liste; forespørgslerne i ensure_superadmin skelnes på deres tekst."""

    def __init__(self, users):
        self.users = users
        self.commits = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.startswith("UPDATE users"):
            keep = stmt.compile().params.get("id_1")
            for u in self.users:
                if u.role == "superadmin" and u.id != keep:
                    u.role = "admin"
            return SimpleNamespace()
        p = stmt.compile().params
        if "lower(users.email)" in sql:
            hit = [u for u in self.users if (u.email or "").lower() == p.get("lower_1") and u.email_verified_at]
            return SimpleNamespace(scalar_one_or_none=lambda: hit[0] if hit else None)
        if "users.obserkode" in sql:
            hit = [u for u in self.users if u.obserkode == p.get("obserkode_1")]
            return SimpleNamespace(scalar_one_or_none=lambda: hit[0] if hit else None)
        others = [u for u in self.users if u.role == "superadmin" and u.id != p.get("id_1")]
        return SimpleNamespace(first=lambda: (others[0].id,) if others else None)

    def add(self, u):
        self.users.append(u)

    async def commit(self):
        self.commits += 1


def su(id, **kw):
    return SimpleNamespace(**{"id": id, "obserkode": f"80{id}ABC", "email": None, "email_verified_at": None, "role": "user",
                              "approved": True, **kw})


@pytest.mark.asyncio
async def test_superadmin_efter_bekraeftet_email(monkeypatch):
    from portal.config import get_settings
    from portal.services import auth

    monkeypatch.setattr(get_settings(), "superadmin_email", " Chef@Example.dk ")
    monkeypatch.setattr(get_settings(), "superadmin_obserkode", "8001ABC")
    old = su(1, role="superadmin")
    unverified = su(2, email="chef@example.dk")
    db = SuperDB([old, unverified])
    await auth.ensure_superadmin(db)
    assert old.role == "superadmin" and unverified.role == "user"  # kun en bekræftet adresse tæller
    unverified.email_verified_at = "2026-11-09"
    unverified.approved = False
    await auth.ensure_superadmin(db)
    assert unverified.role == "superadmin" and unverified.approved and old.role == "admin"  # e-mailen vinder over obserkoden


@pytest.mark.asyncio
async def test_superadmin_kan_fjerne_forbindelse_naar_bundet_til_email(env, monkeypatch):
    from portal.config import get_settings
    from portal.services import dofbinding

    monkeypatch.setattr(get_settings(), "superadmin_email", "anna@example.dk")
    u = user(role="superadmin")
    assert await dofbinding.unbind(DB(u), u, "rigtig-kode-1", "1.1.1.1") == "8220CVH"
    assert u.obserkode == "DT000042"
