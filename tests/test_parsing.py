from datetime import date, datetime, timezone

from backend.app.api.routes.observations import _thread_id, legacy_row_to_ingest
from backend.app.services.dof_sync import _to_ingest_row


def test_legacy_row_to_ingest_maps_danish_columns():
    row = {
        "Obsid": "12345",
        "Artnavn": "Rød glente",
        "Loknavn": "Skagen",
        "Dato": "13-08-2026",
        "Antal": "3",
        "Obserkode": "ABC",
        "Adfkode": "T",
        "Turid": "77",
    }
    ingest = legacy_row_to_ingest(row)
    assert ingest is not None
    assert ingest.obsid == "12345"
    assert ingest.species == "Rød glente"
    assert ingest.count == 3
    assert ingest.observed_at == datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert ingest.is_migration is True
    assert ingest.is_matrikel is True


def test_legacy_row_to_ingest_rejects_incomplete_rows():
    assert legacy_row_to_ingest({"Obsid": "1", "Artnavn": "Musvåge"}) is None
    assert legacy_row_to_ingest({"Obsid": "1", "Artnavn": "Musvåge", "Loknavn": "X", "Dato": "nope"}) is None


def test_dof_sync_row_uses_observation_time_and_defaults():
    ingest = _to_ingest_row(
        {
            "Obsid": "999",
            "Artnavn": "Hedelærke",
            "Loknavn": "Blåvand",
            "Dato": "2026-08-13",
            "Obstidfra": "07:30",
        }
    )
    assert ingest is not None
    assert ingest.observed_at == datetime(2026, 8, 13, 7, 30, tzinfo=timezone.utc)
    assert ingest.category == "alm"
    assert ingest.count is None

    without_time = _to_ingest_row(
        {"Obsid": "1000", "Artnavn": "Musvåge", "Loknavn": "Skagen", "Dato": "13-08-2026"}
    )
    assert without_time is not None
    assert without_time.observed_at.date() == date(2026, 8, 13)
    assert without_time.observed_at.hour == 12


def test_thread_id_prefers_location_id():
    assert _thread_id("Lille Kjove", "42", "Skagen") == "lille-kjove-42"
    assert _thread_id("Lille Kjove", None, "Skagen Nord") == "lille-kjove-skagen-nord"
