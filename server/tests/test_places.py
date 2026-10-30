"""places.filter_and_sort: lokalitetsindeksets filtre og sortering (uden database)."""

import pytest

from portal.services.places import filter_and_sort

ROWS = [
    {"loknr": 1, "navn": "Gedser Odde", "lat": 54.56, "lon": 11.97, "kommune_kode": "0376", "afdeling_id": 5,
     "n_year": 900, "n_taxa": 150, "n_30": 40, "rank30": 2, "last_date": "2026-10-29"},
    {"loknr": 2, "navn": "Amager Fælled", "lat": 55.65, "lon": 12.58, "kommune_kode": "0101", "afdeling_id": 1,
     "n_year": 300, "n_taxa": 90, "n_30": 60, "rank30": 1, "last_date": "2026-10-30"},
    {"loknr": 3, "navn": "Gedser by", "lat": 54.57, "lon": 11.93, "kommune_kode": "0376", "afdeling_id": 5,
     "n_year": 0, "n_taxa": 0, "n_30": 0, "rank30": 0, "last_date": None},
]


def ids(rows):
    return [r["loknr"] for r in rows]


def test_default_sort_is_recent_activity():
    assert ids(filter_and_sort(ROWS)) == [2, 1, 3]


def test_activity_falls_back_to_year_for_other_periods():
    assert ids(filter_and_sort(ROWS, live=False)) == [1, 2, 3]


def test_filters():
    assert ids(filter_and_sort(ROWS, kommune="0376", active=True)) == [1]
    assert ids(filter_and_sort(ROWS, kat="SUB")) == [1]
    assert ids(filter_and_sort(ROWS, afdeling=1)) == [2]


def test_search_puts_prefix_matches_first():
    assert ids(filter_and_sort(ROWS, q="gedser", sort="navn")) == [3, 1]
    assert ids(filter_and_sort(ROWS, q="fælled")) == [2]


def test_distance_requires_position_and_adds_km():
    with pytest.raises(ValueError):
        filter_and_sort(ROWS, sort="afstand")
    rows = filter_and_sort(ROWS, sort="afstand", lat=55.68, lon=12.57)
    assert ids(rows)[0] == 2 and rows[0]["km"] < 5
