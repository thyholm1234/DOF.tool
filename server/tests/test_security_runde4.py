"""Sikkerhedsregressioner fra fjerde runde af gennemgangen 2026-11-08 (docs/sikkerhed/gennemgang-2026-11-08-runde4.md,
kun på serveren). Kører uden database og Valkey: sessionen og Valkey erstattes af små udgaver i hukommelsen."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalars(self):
        return iter(self.rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    """execute(stmt) svarer ud fra et nøgleord i den genererede SQL; get(model, id) fra en ordbog."""

    def __init__(self, answers: dict[str, list] | None = None, objects: dict | None = None):
        self.answers, self.objects, self.committed, self.executed = answers or {}, objects or {}, False, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        for word, rows in self.answers.items():
            if word in sql:
                return Result(rows)
        return Result([])

    async def get(self, model, key):
        return self.objects.get((model.__name__, key))

    def add(self, obj):
        pass

    async def commit(self):
        self.committed = True


# ---------------------------------------------------------------- U-01: «skjul position» og DKU-tip


def test_dku_tipper_ser_ikke_skjult_position():
    from portal.services import dku

    obs = {"skjul_position": True, "fields": {"art": "Rød glente", "position": "55.12345, 12.12345"}}
    assert dku.hides_position("tipper", "portal", obs)
    assert "position" not in dku.without_position(obs["fields"]) and dku.without_position(obs["fields"])["art"] == "Rød glente"


@pytest.mark.parametrize("role,source,obs,hidden", [
    ("dku", "portal", {"skjul_position": True}, False),        # DKU ser positionen
    ("observer", "portal", {"skjul_position": True}, False),   # indtasteren selv
    ("tipper", "portal", {"skjul_position": False}, False),    # ikke skjult
    ("tipper", "dofbasen", {}, False),                         # DOFbasens data har ingen «skjul position»
    ("tipper", "portal", {}, True),                            # øjebliksbillede fra før feltet: regnes som skjult
])
def test_dku_skjul_position_regler(role, source, obs, hidden):
    from portal.services import dku

    assert dku.hides_position(role, source, obs) is hidden


def test_dku_sag_bruger_reglen():
    """get_case skal fjerne positionen fra både øjebliksbilledet og den nuværende observation (og dermed ændringerne)."""
    import inspect

    from portal.api import dku as api

    src = inspect.getsource(api.get_case)
    assert "dku.hides_position(role, case.source, current or snap)" in src
    assert src.index("hides_position") < src.index('"fields":')


def test_dku_oejebliksbillede_har_skjul_position():
    import inspect

    from portal.services import dku

    src = inspect.getsource(dku.observation)
    assert "r.skjul_position" in src and '"skjul_position": bool(r["skjul_position"])' in src


# ---------------------------------------------------------------- U-03: «roller» kan ikke give mere end man selv har


@pytest.mark.parametrize("module", ["dofbasen", "netfugl"])
async def test_ungrantable_ny_rolle(module):
    import importlib

    svc = importlib.import_module(f"portal.services.{module}")
    assert await svc.ungrantable(None, {"roller"}, perms=["roller"]) == set()
    assert await svc.ungrantable(None, {"roller"}, perms=["roller", "api"]) == {"api"}


@pytest.mark.parametrize("module,admin_perms", [("dofbasen", ["sider", "nyheder", "lister", "dku", "api", "roller"]),
                                                ("netfugl", ["artikler", "fotos", "api", "roller"])])
async def test_ungrantable_tildeling_og_fjernelse(module, admin_perms):
    import importlib

    svc = importlib.import_module(f"portal.services.{module}")
    db = FakeDB({"user_roles": ["roller_rolle"], ".perms": [admin_perms]})
    # Give sig selv administratorrollen: rollen har rettigheder, man ikke har
    assert await svc.ungrantable(db, {"roller"}, user_id=7, roles=["roller_rolle", "admin"]) == set(admin_perms) - {"roller"}
    # Uændrede roller: intet at tjekke (ingen opslag af rollers rettigheder)
    db = FakeDB({"user_roles": ["roller_rolle"]})
    assert await svc.ungrantable(db, {"roller"}, user_id=7, roles=["roller_rolle"]) == set()


@pytest.mark.parametrize("module", ["dofbasen", "netfugl"])
async def test_saet_roller_afviser_eskalering(module, monkeypatch):
    import importlib

    api = importlib.import_module(f"portal.api.{module}")
    svc = importlib.import_module(f"portal.services.{module}")
    called = []

    async def no_set(*a, **k):
        called.append(a)

    monkeypatch.setattr(svc, "set_user_roles", no_set)
    db = FakeDB({"user_roles": [], ".perms": [["api", "roller"]]}, {("User", 7): SimpleNamespace(id=7, obserkode="ABC")})
    ctx = SimpleNamespace(perms={"roller"}, user=SimpleNamespace(id=1))
    with pytest.raises(HTTPException) as exc:
        await api.saet_roller(user_id=7, body=api.UserRolesIn(roles=["admin"]), ctx=ctx, db=db)
    assert exc.value.status_code == 403 and not called and not db.committed


@pytest.mark.parametrize("module,model", [("dofbasen", "DofbRole"), ("netfugl", "NfRole")])
async def test_ret_rolle_afviser_eskalering(module, model):
    import importlib

    api = importlib.import_module(f"portal.api.{module}")
    role = SimpleNamespace(key="r", name="R", perms=["roller"])
    db = FakeDB({".perms": [["roller"]]}, {(model, "r"): role})
    ctx = SimpleNamespace(perms={"roller"}, user=SimpleNamespace(id=1))
    with pytest.raises(HTTPException) as exc:
        await api.ret_rolle(key="r", body=api.RoleIn(name="R", perms=["roller", "api"]), ctx=ctx, db=db)
    assert exc.value.status_code == 403 and role.perms == ["roller"] and not db.committed


# ---------------------------------------------------------------- U-04: grupper og skjulte lister


async def test_gruppe_kan_ikke_tilfoeje_bruger_med_skjulte_lister(monkeypatch):
    from portal.api import lists as api

    owner = SimpleNamespace(id=1, obserkode="EJER1")
    hidden = SimpleNamespace(id=2, obserkode="SKJULT", prefs={"lists_public": False})
    db = FakeDB({"FROM users": [hidden]}, {("ListGroup", 5): SimpleNamespace(id=5, owner_id=1)})
    with pytest.raises(HTTPException) as exc:
        await api.add_member(group_id=5, body={"obserkode": "skjult"}, user=owner, db=db)
    assert exc.value.status_code == 403 and not db.committed


async def test_gruppe_kan_tilfoeje_offentlig_bruger_og_ikke_portalbruger(monkeypatch):
    from portal.api import lists as api

    async def no_bump(*_):
        return None

    monkeypatch.setattr(api, "bump", no_bump)
    owner = SimpleNamespace(id=1, obserkode="EJER1")
    for answers in ({"FROM users": [SimpleNamespace(id=3, obserkode="AABEN", prefs={})]}, {}):
        db = FakeDB(answers, {("ListGroup", 5): SimpleNamespace(id=5, owner_id=1)})
        assert await api.add_member(group_id=5, body={"obserkode": "AABEN"}, user=owner, db=db) == {"ok": True}
        assert db.committed


# ---------------------------------------------------------------- U-05: kommentarer til upublicerede artikler


async def test_artikelkommentarer_kraever_synlig_artikel():
    from portal.services import article_comments

    db = FakeDB({"FROM art_articles": [{"id": 9, "status": "kladde", "comments_open": True, "title": "x"}]})
    with pytest.raises(article_comments.CommentError) as exc:
        await article_comments.article_visible(db, 9, can_edit=False)
    assert exc.value.status == 404
    assert (await article_comments.article_visible(db, 9, can_edit=True))["id"] == 9


async def test_kommentarlisten_tjekker_artiklen(monkeypatch):
    from portal.api import artikler as api
    from portal.services import article_comments

    listed = []

    async def hidden(*_a, **_k):
        raise article_comments.CommentError(404, "Artiklen findes ikke")

    async def lst(*a, **k):
        listed.append(a)
        return {}

    monkeypatch.setattr(article_comments, "article_visible", hidden)
    monkeypatch.setattr(article_comments, "list_comments", lst)
    with pytest.raises(HTTPException) as exc:
        await api.kommentarer(aid=9, user=SimpleNamespace(id=1), acc=SimpleNamespace(can_edit=False, can_moderate=False), db=None)
    assert exc.value.status_code == 404 and not listed


# ---------------------------------------------------------------- U-06: audit-log for rettighedsændringer


@pytest.fixture
def tracked(monkeypatch):
    from portal.api.admin import brugere

    events = []

    async def track(uid, kind, path=None, detail=None):
        events.append((kind, detail))

    async def no_bump(*_):
        return None

    monkeypatch.setattr(brugere.activity, "track", track)
    monkeypatch.setattr(brugere, "bump", no_bump)
    return events


async def test_rolleaendring_logges_efter_commit(tracked):
    from portal.api.admin import brugere

    admin = SimpleNamespace(id=1, role="superadmin", permissions=None, blocked=False)
    target = SimpleNamespace(id=2, role="user", permissions=None, blocked=False, approved=True)
    db = FakeDB(objects={("User", 2): target})
    await brugere.update_user(user_id=2, body={"role": "moderator", "permissions": ["moderation"]}, user=admin, db=db)
    assert db.committed
    assert ("role", {"user": 2, "from": "user", "to": "moderator"}) in tracked
    assert ("permissions", {"user": 2, "from": None, "to": ["moderation"]}) in tracked


async def test_afvist_rolleaendring_logges_ikke(tracked):
    from portal.api.admin import brugere

    me = SimpleNamespace(id=1, role="moderator", permissions=["brugere"], blocked=False)
    target = SimpleNamespace(id=2, role="user", permissions=None, blocked=False, approved=True)
    db = FakeDB(objects={("User", 2): target})
    with pytest.raises(HTTPException):
        await brugere.update_user(user_id=2, body={"role": "moderator", "permissions": ["drift"]}, user=me, db=db)
    assert not tracked and not db.committed


# ---------------------------------------------------------------- U-07: positionstokens indgår aldrig råt i en nøgle


class KV:
    def __init__(self, data):
        self.d = dict(data)

    async def get(self, k):
        return self.d.get(k)

    async def exists(self, *ks):
        return sum(k in self.d for k in ks)

    async def ttl(self, k):
        return 100

    async def set(self, k, v, ex=None, nx=False):
        self.d[k] = v

    async def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)


@pytest.fixture
def loc_kv(monkeypatch):
    from portal.services import position_privacy as pp

    real = "x" * 43
    store = KV({pp.token_key(real): "7:device-abc", "loctok:user:7": pp.token_key(real),
                "loctok:user:7:sess": "loctok:sess:abc", "loctok:sess:abc": "7"})
    monkeypatch.setattr(pp, "kv", lambda: store)
    return store, real


async def test_hash_som_token_giver_ikke_adgang(loc_kv):
    from portal.services import position_privacy as pp

    store, real = loc_kv
    before = dict(store.d)
    stolen = pp.token_key(real).removeprefix("loctok:")  # «h:<sha256>», som det står i en kopi af Valkey
    assert await pp.token_owner(stolen) is None
    assert store.d == before  # den rigtige nøgle er hverken flyttet eller slettet


@pytest.mark.parametrize("token", ["user:7", "user:7:sess", "sess:abc", "h:" + "0" * 64, "../x", "a" * 101, ""])
async def test_nøglenavne_som_token_rører_intet(loc_kv, token):
    from portal.services import position_privacy as pp

    store, real = loc_kv
    before = dict(store.d)
    assert await pp.token_owner(token) is None
    assert store.d == before  # før rettelsen slettede «user:7» pegeren, så tokenet ikke kunne tilbagekaldes
    assert await pp.token_owner(real) == "7:device-abc"


# ---------------------------------------------------------------- U-10: medforfattere


async def test_medforfatter_kan_ikke_fjerne_andre(monkeypatch):
    from portal.api import learn_build as api

    async def course(db, user, slug):
        return SimpleNamespace(id=4, created_by=99)

    monkeypatch.setattr(api, "_course", course)
    editor = SimpleNamespace(id=5, role="user", permissions=["laering_undervis"], blocked=False)
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await api.fjern_medforfatter(slug="k", uid=6, user=editor, db=db)
    assert exc.value.status_code == 403 and not db.executed
    assert await api.fjern_medforfatter(slug="k", uid=5, user=editor, db=db) == {"ok": True}  # sig selv: tilladt
    owner = SimpleNamespace(id=99, role="user", permissions=["laering_undervis"], blocked=False)
    assert await api.fjern_medforfatter(slug="k", uid=6, user=owner, db=FakeDB()) == {"ok": True}


# ---------------------------------------------------------------- lydquiz: id'er fra xeno-canto bliver til filnavne


@pytest.mark.parametrize("source,ext_id", [("xc", "../../etc/x"), ("xc", "1/2"), ("ml", "12a"), ("evil", "123"), ("xc", "")])
async def test_lydfil_afviser_ugyldige_id(source, ext_id):
    from portal.services import sound_quiz

    with pytest.raises(sound_quiz.SoundError):
        await sound_quiz.audio_file(source, ext_id, "https://example.invalid/x.mp3")


def test_xeno_canto_svar_med_ugyldigt_id_springes_over():
    from portal.services import sound_quiz

    rec = {"gen": "Parus", "sp": "major", "file": "https://xeno-canto.org/1/download", "length": "0:30"}
    out = sound_quiz._parse({"recordings": [{**rec, "id": "../x"}, {**rec, "id": "123"}]}, "Parus major", 120)
    assert [r["ext_id"] for r in out] == ["123"]


# ---------------------------------------------------------------- U-02: indtastning på vegne af andre er fjernet


@pytest.mark.parametrize("existing,code,expected", [
    (None, None, "MIN1"),          # ny tur: brugerens egen kode
    (None, "min1", "MIN1"),        # egen kode angivet
    ("ANDEN", "ANDEN", "ANDEN"),   # tur fra før fjernelsen: beholder sin obserkode, når den rettes
    ("ANDEN", None, "ANDEN"),
    ("MIN1", None, "MIN1"),
])
async def test_observatoer_er_brugeren_selv(existing, code, expected):
    from portal.api import trips

    user = SimpleNamespace(obserkode="MIN1")
    assert await trips._observer_code(user, SimpleNamespace(obserkode=existing), code) == expected


@pytest.mark.parametrize("existing", [None, "MIN1", "ANDEN"])
async def test_ny_anden_obserkode_afvises(existing):
    from portal.api import trips

    with pytest.raises(HTTPException) as exc:
        await trips._observer_code(SimpleNamespace(obserkode="MIN1"), SimpleNamespace(obserkode=existing), "OFFER1")
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- U-12: se som bruger får ikke personlige notifikationer


async def test_se_som_abonnerer_ikke_paa_maalbrugerens_kanal(monkeypatch):
    from portal.api import feed

    seen = []

    async def events(request, channels):
        seen.append(channels)
        yield ""

    monkeypatch.setattr(feed, "_open_streams", {})
    monkeypatch.setattr(feed, "_events", events)
    target = SimpleNamespace(id=42)
    for state, expected in ((SimpleNamespace(view_as={"user_id": 42}), ["feed"]), (SimpleNamespace(), ["feed", "user:42"])):
        resp = await feed.stream(request=SimpleNamespace(state=state), user=target)
        async for _ in resp.body_iterator:
            pass
        assert seen.pop() == expected
