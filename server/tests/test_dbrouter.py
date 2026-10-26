"""Opdelingen i databasegrupper: hver tabel hører til én gruppe, ingen fremmednøgle krydser grupper, og SQL routes rigtigt."""

import pytest

from portal import models
from portal.db import Base
from portal.dbgroups import ALL_TABLES, OBS_TABLES, OBSERVATIONS, REFERENCE_TABLES, USER_TABLES, USERS, group_of_sql, group_of_table, tables_in
from portal.dbops import ddl_groups, tables_for
from portal.dbrouter import RoutingSession, is_read_sql


def test_every_table_is_classified_once():
    names = {t.name for t in Base.metadata.sorted_tables}
    assert names == ALL_TABLES
    assert not (OBS_TABLES & USER_TABLES or OBS_TABLES & REFERENCE_TABLES or USER_TABLES & REFERENCE_TABLES)


def test_no_foreign_key_crosses_groups():
    for group in (USERS, OBSERVATIONS):
        allowed = tables_for(group)
        for t in Base.metadata.sorted_tables:
            if t.name in allowed:
                for fk in t.foreign_keys:
                    assert fk.column.table.name in allowed, f"{t.name} -> {fk.column.table.name} krydser grupper"


def test_extra_ddl_has_a_target_group():
    for ddl in models.EXTRA_DDL:
        assert ddl_groups(ddl)


def test_group_of_sql():
    assert group_of_sql("SELECT * FROM observations o JOIN species s ON s.artnr = o.artnr") == OBSERVATIONS
    assert group_of_sql("SELECT * FROM users u JOIN locations l ON l.loknr = 1") == USERS
    assert group_of_sql("SELECT * FROM locations") is None
    assert group_of_sql("SELECT 1 FROM ix_obs_artnr_dato") is None  # indeksnavne ligner ikke tabelnavne
    with pytest.raises(ValueError):
        group_of_sql("SELECT 1 FROM users u JOIN observations o ON o.obserkode = u.obserkode")
    assert tables_in("select count(*) from trips") == {"trips"}


def test_read_write_classification():
    assert is_read_sql("SELECT * FROM observations")
    assert is_read_sql("  WITH x AS (SELECT 1) SELECT * FROM x")
    assert not is_read_sql("INSERT INTO day_sync VALUES (1)")
    assert not is_read_sql("WITH d AS (DELETE FROM notifications RETURNING 1) SELECT count(*) FROM d")
    assert not is_read_sql("SELECT nextval('x')")
    assert not is_read_sql("SELECT pg_advisory_lock(1)")
    assert not is_read_sql("SELECT * FROM users FOR UPDATE")


def test_routing_session_picks_group_and_primary_after_write():
    from sqlalchemy import text
    s = RoutingSession()
    assert s._classify(None, text("SELECT * FROM observations")) == (OBSERVATIONS, False)
    assert s._classify(None, text("UPDATE users SET navn = 'x'")) == (USERS, True)
    assert s._classify(None, text("SELECT * FROM species")) == (USERS, False)  # kun referencedata: sessionens hjemgruppe
    assert s._classify(models.Observation.__mapper__, None) == (OBSERVATIONS, False)
    s.info["home"] = OBSERVATIONS
    assert s._classify(None, text("SELECT * FROM species")) == (OBSERVATIONS, False)
    assert group_of_table("trips") == OBSERVATIONS and group_of_table("species") is None


def test_only_read_replicas_serve_reads():
    from portal.dbrouter import Node, Router
    r = Router()
    mk = lambda i, role, purpose: Node(i, f"n{i}", OBSERVATIONS, role, "h", 5432, "portal", "portal", "t", True, purpose)
    r.nodes = [mk(1, "primary", "read"), mk(2, "replica", "backup"), mk(3, "replica", "read")]
    r.read_replicas = {USERS: False, OBSERVATIONS: True}
    picked = {r.reader(OBSERVATIONS) for _ in range(6)}
    assert picked == {r.engine_for(r.nodes[2])}  # backup-replikaen (2) bruges aldrig
    r.nodes = [mk(1, "primary", "read"), mk(2, "replica", "backup")]
    assert r.reader(OBSERVATIONS) is r.primary(OBSERVATIONS)  # kun backup tilbage: primary læser


def test_area_scope_with_geom_stays_in_observations():
    from portal.services.areas import Scope
    sc = Scope("area", "7")
    pred, _ = sc.predicate("o")
    assert group_of_sql(pred) == USERS  # uden geometri joines areas – kun til brugergruppen (feltmeldinger)
    pred, params = Scope("area", "7", geom="SRID=4326;POINT(12 55)").predicate("o")
    assert group_of_sql("SELECT 1 FROM observations o WHERE " + pred) == OBSERVATIONS and params == {"scope_ref_geom": "SRID=4326;POINT(12 55)"}
    assert Scope("area", "7", geom="").predicate("o") == ("FALSE", {})
    assert Scope("area", "7", geom="x") == sc and Scope("area", "7", geom="x").key == sc.key


def test_orm_dml_and_locking_selects_go_to_primary():
    from sqlalchemy import delete, select, update
    from portal.models import Notification
    s = RoutingSession()
    mapper = Notification.__mapper__
    assert s._classify(mapper, update(Notification).values(read=True)) == (USERS, True)  # gik før til replikaen
    assert s._classify(mapper, delete(Notification)) == (USERS, True)
    assert s._classify(mapper, select(Notification)) == (USERS, False)
    assert s._classify(mapper, select(Notification).with_for_update()) == (USERS, True)
    assert s._classify(None, select(Notification).with_for_update()) == (USERS, True)


def test_no_assembled_sql_mixes_groups():
    """SQL bygget af stumper i variabler (fx joins = "...") må heller ikke blande grupperne – det fanger router-tjekket
    først, når sætningen køres. Variablerne slås op i funktionens egne tildelinger."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "portal"

    def consts(node):
        return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    bad = []
    for f in root.rglob("*.py"):
        for fn in ast.walk(ast.parse(f.read_text())):
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            assigned: dict[str, list[str]] = {}
            for n in ast.walk(fn):
                if isinstance(n, (ast.Assign, ast.AugAssign)):
                    for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
                        for nm in ast.walk(t):
                            if isinstance(nm, ast.Name):
                                assigned.setdefault(nm.id, []).append(" ".join(consts(n.value)))
            for n in ast.walk(fn):
                if not isinstance(n, ast.JoinedStr):
                    continue
                base = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
                if not any(k in base.upper() for k in ("SELECT", "UPDATE ", "INSERT", "DELETE")):
                    continue
                extra = [x for v in n.values if isinstance(v, ast.FormattedValue) for nm in ast.walk(v.value)
                         if isinstance(nm, ast.Name) for x in assigned.get(nm.id, [])]
                if len({group_of_table(t) for t in tables_in(base + " " + " ".join(extra))} - {None}) > 1:
                    bad.append(f"{f.relative_to(root)}:{n.lineno} {fn.name}")
    assert not bad, bad


def test_reads_stay_on_primary_after_commit(monkeypatch):
    from sqlalchemy import text

    from portal.dbrouter import Node, router
    mk = lambda i, role: Node(i, f"n{i}", OBSERVATIONS, role, "h", 5432, "portal", "portal", f"t{i}")
    monkeypatch.setattr(router, "nodes", [mk(1, "primary"), mk(2, "replica")])
    monkeypatch.setattr(router, "read_replicas", {USERS: False, OBSERVATIONS: True})
    primary, replica = router.engine_for(router.nodes[0]).sync_engine, router.engine_for(router.nodes[1]).sync_engine
    s = RoutingSession()
    read = text("SELECT * FROM observations")
    assert s.get_bind(clause=read) is replica
    s.get_bind(clause=text("UPDATE observations SET antal = 1"))
    s.commit()
    assert s.get_bind(clause=read) is primary  # commit og refresh skal kunne se sin egen skrivning
    s.close()
    assert s.get_bind(clause=read) is replica
