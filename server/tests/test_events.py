"""Hjælpere i turoversigten (services/dof_events.py)."""

from portal.services.dof_events import event_url, hhmm


def test_event_url_only_for_dof_events():
    assert event_url({"id": 42, "source": "dof"}).endswith("?event_id=42")
    assert event_url({"id": 7, "source": "portal"}) is None


def test_hhmm():
    assert hhmm("07.30") == "07:30" and hhmm("18:05") == "18:05" and hhmm(None) is None
