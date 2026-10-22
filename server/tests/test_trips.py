"""Tur-indtastning: validering af den samlede tur (se portal/api/trips.py)."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from portal.api.trips import TripIn

BASE = {"dato": date.today().isoformat(), "loknr": 765013}


def test_minimal_trip_and_children():
    t = TripIn(**BASE, turtidfra="10.00", observations=[{"artnr": 1860, "antal": 3, "adfkode": "FU"}],
               breeding_pairs=[{"artnr": 1860, "adfkode": "YP"}], disturbances=[{"type_id": 13, "antal": 2}])
    assert t.metode_id == 1 and t.observations[0].maerker == [] and t.breeding_pairs[0].antal == 1


@pytest.mark.parametrize("patch", [
    {"dato": (date.today() + timedelta(days=1)).isoformat()},
    {"turtidfra": "12:00", "turtidtil": "11:59"},
    {"metode_id": 9999},
    {"vindretning": "X"},
    {"nedboertype": "Tåge"},
    {"skydaekke": 9},
    {"on_behalf_of": "ikke gyldig!"},
    {"gps_track": [[10.0, 10.0, 1, 5]]},
    {"disturbances": [{"type_id": 9999}]},
    {"observations": [{"artnr": 1, "maerker": ["findes_ikke"]}]},
])
def test_invalid_trip_is_rejected(patch):
    with pytest.raises(ValidationError):
        TripIn(**{**BASE, **patch})


def test_end_time_may_equal_start():
    TripIn(**BASE, turtidfra="10:00", turtidtil="10:00", gps_track=[[57.1, 8.6, 1, 5]])
