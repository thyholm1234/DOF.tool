"""Lokalitetsimport: kontrol af zip'en og af hver lokalitet (services/location_import.py) – uden database."""

import hashlib
import json
import zipfile

import pytest

from portal.services.location_import import ImportError_, clean_border, clean_record, open_zip, read_records

SQUARE = {"type": "Polygon", "coordinates": [[[9.0, 56.0], [9.1, 56.0], [9.1, 56.1], [9.0, 56.1], [9.0, 56.0]]],
          "oprettet": "2018-03-10T18:25:39Z", "accepteret": "nej", "punkter": 5}


def record(loknr=663010, **kw):
    return {"schema": 1, "loknr": loknr, "lokid": 6243, "navn": "Bølling Sø", "aktiv": True, "vis": 1,
            "center": {"lat": 56.05, "lon": 9.05}, "graense": SQUARE, "kommuner": ["Ikast-Brande", "Silkeborg"],
            "lokalafdelinger": ["DOF Vestjylland"], "region": "Region Midtjylland", "typer_tekst": "Sø/Permanent vandhul/mose",
            "beskrivelse": "Højmose og eng.", "iba": {"id": 133, "navn": "Bølling Sø"},
            "dofbasen": {"arter": 209, "obsdage": 3252, "observationer": 51151},
            "felter": {"Region": "Region Midtjylland"}, "hentet": {"side": "2026-10-31T01:20:00+01:00"}, **kw}


def make_zip(path, records, extra=None, bad_sum=False, manifest=None):
    files = {f"lokaliteter/{str(r['loknr'])[:3]}/{r['loknr']}.json": json.dumps(r).encode() for r in records}
    files.update(extra or {})
    sums = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    if bad_sum:
        sums[next(iter(sums))] = "0" * 64
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest or {"schema": 1, "filer": sums}))
        for n, b in files.items():
            z.writestr(n, b)
    return path


def test_valid_zip_reads_records(tmp_path):
    p = make_zip(tmp_path / "ok.zip", [record(), record(663011, graense=None)])
    manifest, rows, rejected = read_records(p)
    assert manifest["schema"] == 1 and not rejected and len(rows) == 2
    r = next(x for x in rows if x["loknr"] == 663010)
    assert r["kommuner"] == ["Ikast-Brande", "Silkeborg"] and r["iba_id"] == 133 and r["har_side"]
    assert r["_punkter"] == 5 and r["_meta"] == {"oprettet": "2018-03-10T18:25:39Z", "accepteret": "nej"}
    assert json.loads(r["_geojson"])["type"] == "Polygon"
    assert next(x for x in rows if x["loknr"] == 663011)["_geojson"] is None


@pytest.mark.parametrize("name", ["../evil.json", "/etc/passwd", "lokaliteter/663/../../x.json", "noget/andet.txt"])
def test_unexpected_names_are_rejected(tmp_path, name):
    with pytest.raises(ImportError_):
        open_zip(make_zip(tmp_path / "x.zip", [record()], extra={name: b"{}"}))


def test_manifest_required_and_schema_checked(tmp_path):
    p = tmp_path / "nomani.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("lokaliteter/663/663010.json", json.dumps(record()))
    with pytest.raises(ImportError_, match="manifest"):
        open_zip(p)
    with pytest.raises(ImportError_, match="skemaversion"):
        open_zip(make_zip(tmp_path / "v2.zip", [record()], manifest={"schema": 2, "filer": {}}))


def test_checksum_mismatch_rejects_the_record(tmp_path):
    _, rows, rejected = read_records(make_zip(tmp_path / "sum.zip", [record()], bad_sum=True))
    assert not rows and "kontrolsummen" in rejected[0][1]


def test_not_a_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"ikke en zip")
    with pytest.raises(ImportError_, match="gyldig zip"):
        open_zip(p)


def test_record_validation():
    with pytest.raises(ValueError, match="filnavnet"):
        clean_record(record(), 999)
    with pytest.raises(ValueError, match="navn"):
        clean_record(record(navn=""), 663010)
    with pytest.raises(ValueError):
        clean_record(record(kommuner="København"), 663010)
    long = clean_record(record(beskrivelse="x" * 30_000), 663010)
    assert len(long["beskrivelse"]) == 20_000


def test_border_validation():
    assert clean_border(None) == (None, 0, None)
    with pytest.raises(ValueError, match="polygon"):
        clean_border({"type": "Point", "coordinates": [9, 56]})
    with pytest.raises(ValueError, match="færre end 4"):
        clean_border({"type": "Polygon", "coordinates": [[[9, 56], [9.1, 56], [9, 56]]]})
    with pytest.raises(ValueError, match="koordinat"):
        clean_border({"type": "Polygon", "coordinates": [[[9, 56], [9.1, 56], [9.1, 999], [9, 56]]]})
    multi = {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"], SQUARE["coordinates"]]}
    assert clean_border(multi)[1] == 10
