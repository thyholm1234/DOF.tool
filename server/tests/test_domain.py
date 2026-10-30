"""Domæneregler i portal.domain: kategorier, adfærd/træk og geometri."""

import pytest

from portal.domain import adfaerd, geo, kategori


def test_kategori_rank_and_levels():
    assert kategori.rank("SU") > kategori.rank("SUB") > kategori.rank("bemaerk") > kategori.rank("alm") == kategori.rank(None)
    assert kategori.from_rank(3) == "SU" and kategori.from_rank(None) == "alm"
    assert kategori.higher("SUB", "SU") == "SU" and kategori.higher("bemaerk", "alm") == "bemaerk"
    assert kategori.at_least("SUB") == ["SU", "SUB"]
    assert kategori.at_least("bemaerk") == ["SU", "SUB", "bemaerk"]


def test_kategori_sql_matches_python():
    # Samme udtryk som før omlægningen (feed, locyear)
    assert kategori.rank_sql("o.kategori") == "CASE o.kategori WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 WHEN 'bemaerk' THEN 1 ELSE 0 END"


@pytest.mark.parametrize("code,first,last", [
    ("N", True, True), ("T R", True, False), ("R T", False, True), ("TF", True, False), ("OF", False, True),
    ("R SV", False, True), ("N R", True, False), ("T HØ", True, True), ("", False, False), (None, False, False), ("  ", False, False),
])
def test_two_migration_definitions(code, first, last):
    assert adfaerd.first_is_migration(code) is first
    assert adfaerd.is_migrating(code) is last


def test_migration_direction():
    assert adfaerd.migration("R SSV") == (True, 202.5)
    assert adfaerd.migration("FU T") == (True, None)


def test_migration_sql_unchanged():
    assert adfaerd.first_migration_sql("o.adfkode") == (
        "split_part(coalesce(o.adfkode, ''), ' ', 1) IN ('T','TF','N','NØ','Ø','SØ','S','SV','V','NV')")
    assert adfaerd.last_migration_sql("o.adfkode") == (
        r"coalesce(substring(regexp_replace(coalesce(o.adfkode, ''), '(\s+HØ)+\s*$', '') from '(\S+)\s*$') "
        "= ANY(CAST(:mig AS text[])), false)")


def test_geo():
    cph, aarhus = (55.676, 12.568), (56.157, 10.211)
    assert 155 < geo.km(cph, aarhus) < 160
    assert geo.km(cph, cph) == 0
    assert round(geo.bearing((56.0, 10.0), (57.0, 10.0))) == 0
    assert round(geo.bearing((56.0, 10.0), (55.0, 10.0))) == 180
    assert geo.angle_diff(350, 10) == 20 and geo.angle_diff(0, 180) == 180
