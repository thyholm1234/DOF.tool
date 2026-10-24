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
