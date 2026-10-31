"""location_edit: rene hjælpere (forenkling til kortet og kontrol af klientens grænse) – uden database."""

import json

import pytest

from portal.services.location_edit import EditError, _geojson_in, cur, tolerance

SQ = {"type": "Polygon", "coordinates": [[[9.0, 56.0], [9.1, 56.0], [9.1, 56.1], [9.0, 56.1], [9.0, 56.0]]]}


def test_tolerance_halves_per_zoom_and_is_exact_from_16():
    assert tolerance(12) == pytest.approx(2 * tolerance(13))
    assert tolerance(16) == 0 and tolerance(20) == 0
    assert tolerance(10) < 0.001  # under en tusindedel grad (~100 m) ved laveste viste zoom


def test_geojson_in_accepts_feature_and_geometry():
    assert json.loads(_geojson_in(SQ))["type"] == "Polygon"
    assert json.loads(_geojson_in({"type": "Feature", "geometry": SQ, "properties": {}}))["type"] == "Polygon"


@pytest.mark.parametrize("bad", [None, {"type": "Point", "coordinates": [9, 56]}, {"type": "Polygon", "coordinates": [[[9, 56], [9, 57], [9, 56]]]}])
def test_geojson_in_rejects(bad):
    with pytest.raises(EditError):
        _geojson_in(bad)


def test_cur_qualifies_both_columns():
    assert cur("s") == "s.valid_to IS NULL AND s.status = 'aktiv'"
