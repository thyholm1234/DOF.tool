"""Nye arter og underarter fra DOFbasen (dof/newspecies.py, dof/ingest.py): portalen opretter arten, bestiller
artsliste og SU/SUB-status med det samme og regner kategorien igen, så en ny art for Danmark giver en SU-alarm."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from portal.dof import newspecies
from portal.dof.classify import Classifier
from portal.dof.ingest import guess_art_type

TODAY = date(2026, 11, 9)


class KV:
    def __init__(self):
        self.z: dict[str, dict[str, float]] = {}
        self.ver: dict[str, int] = {}

    async def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    async def zrange(self, k, a, b):
        return sorted(self.z.get(k, {}), key=lambda m: self.z[k][m])

    async def zremrangebyscore(self, k, lo, hi):
        for m, sc in list(self.z.get(k, {}).items()):
            if lo <= sc <= hi:
                del self.z[k][m]


class Session:
    def __init__(self, species=(), obs=()):
        self.species, self.obs, self.updates, self.commits = list(species), list(obs), [], 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.lstrip().startswith("UPDATE observations"):
            self.updates.extend(params)
            return SimpleNamespace()
        if "FROM observations" in sql:
            return SimpleNamespace(all=lambda: self.obs)
        return SimpleNamespace(all=lambda: self.species)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def env(monkeypatch):
    store, enq, bumps, alerted = KV(), [], [], []

    async def enqueue(kind, params=None, *, priority=2, dedupe=None, secret=None):
        enq.append((kind, params, priority, dedupe))
        return f"job{len(enq)}"

    async def bump(name):
        bumps.append(name)

    async def publish(*a, **k):
        pass

    async def process(session, new_obsids, changed, extra=None):
        alerted.extend(new_obsids)

    from portal.services import alerts

    monkeypatch.setattr(newspecies.gateway, "enqueue", enqueue)
    monkeypatch.setattr(newspecies, "bump", bump)
    monkeypatch.setattr(newspecies, "publish", publish)
    monkeypatch.setattr(newspecies, "dk_today", lambda: TODAY)
    monkeypatch.setattr(newspecies, "SessionLocal", lambda: Session())
    monkeypatch.setattr(alerts, "process", process)
    return SimpleNamespace(kv=store, enq=enq, bumps=bumps, alerted=alerted, mp=monkeypatch)


def test_foreloebig_type_ud_fra_navnet():
    assert guess_art_type("Sibirisk Jernspurv") == "art"
    assert guess_art_type("Gulbug (ssp. flava)") == "underart"
    assert guess_art_type("Gråand x Spidsand") == "hybrid"
    assert guess_art_type("Albatros sp.") == "ub"
    assert guess_art_type("Hav-/Fjordterne") == "ub"


async def test_ny_art_hentes_straks_i_opslagsbanen(env):
    env.mp.setattr(newspecies, "ObsSession", lambda: Session(species=[(9001, "Sibirisk Jernspurv", "art"), (9002, "Gråand x Spidsand", "hybrid")]))
    await newspecies.discovered({9001, 9002})
    assert env.enq == [("new_species", {"artnrs": [9001, 9002], "lists": ["art", "hybrid"]}, newspecies.gateway.INTERACTIVE,
                        "new_species:9001,9002")]
    assert "new_species" in newspecies.gateway.LOOKUP_KINDS  # udføres med det samme, uden udtrækkenes interval
    assert "reference" in env.bumps  # klassifikatoren genindlæses – arten «opdages» ikke igen ved næste live-hentning


async def test_ny_su_art_giver_alarm_for_dagens_observation(env):
    obs = [(1, 9001, "Sibirisk Jernspurv", TODAY, 1, [3], "alm"),
           (2, 9001, "Sibirisk Jernspurv", date(1999, 5, 1), 1, [3], "alm"),
           (3, 9001, "Sibirisk Jernspurv", TODAY, 1, [3], "SU")]
    sess = Session(obs=obs)
    env.mp.setattr(newspecies, "ObsSession", lambda: sess)

    async def clf(_s):
        return Classifier(klass_by_artnr={9001: "SU"})

    env.mp.setattr(newspecies, "get_classifier", clf)
    r = await newspecies.reclassify({9001})
    assert sess.updates == [{"o": 1, "k": "SU"}, {"o": 2, "k": "SU"}]  # alle rettes, også gamle (ingen tidsgrænse) …
    assert env.alerted == [1]  # … men kun dagens giver alarm
    assert r == {"species": 1, "changed": 2, "upgraded": 1}
    assert "feed" in env.bumps and "lists" in env.bumps


async def test_uden_arter_sker_intet(env):
    env.mp.setattr(newspecies, "ObsSession", lambda: pytest.fail("ingen databasekald uden arter"))
    assert (await newspecies.reclassify(set()))["species"] == 0


async def test_aendret_klassifikation_findes(env):
    snaps = iter([[(1, "Alm"), (2, "SUB"), (3, "Alm")], [(1, "SU"), (2, "SUB"), (3, "Alm"), (4, "Alm")]])
    env.mp.setattr(newspecies, "ObsSession", lambda: Session(species=next(snaps)))
    before = await newspecies.klass_snapshot()
    assert await newspecies.changed_since(before) == {1, 4}


async def test_job_new_species_henter_lister_og_regner_igen(monkeypatch):
    from portal.dof import jobs

    calls, reclassified = [], []

    async def artdata(client, kind):
        calls.append(("artdata", kind))

    async def status(client, klass):
        calls.append(("status", klass))

    snaps = iter([{1: "Alm", 9001: "Alm"}, {1: "SU", 9001: "SU"}])

    async def snapshot():
        return next(snaps)

    async def changed_since(before):
        after = await snapshot()
        return {a for a, k in after.items() if before.get(a) != k}

    async def reclassify(artnrs):
        reclassified.append(set(artnrs))
        return {}

    monkeypatch.setattr(jobs, "_artdata", artdata)
    monkeypatch.setattr(jobs, "_status", status)
    monkeypatch.setattr(jobs.newspecies, "klass_snapshot", snapshot)
    monkeypatch.setattr(jobs.newspecies, "changed_since", changed_since)
    monkeypatch.setattr(jobs.newspecies, "reclassify", reclassify)
    await jobs.job_new_species(None, {"artnrs": [9001], "lists": ["art"]})
    assert calls == [("artdata", "art"), ("status", "SU"), ("status", "SUB")]
    assert reclassified == [{9001, 1}]
