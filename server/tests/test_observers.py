"""Observatørsider: hvem må se en side (services/observers.visible_to)."""

from types import SimpleNamespace

from portal.services.observers import visible_to


def viewer(uid=2, role="user"):
    return SimpleNamespace(id=uid, role=role, obserkode="X")


def test_dofbasen_observers_and_public_users_are_visible():
    assert visible_to(None, viewer())
    assert visible_to({"id": 1, "prefs": {}}, viewer())
    assert visible_to({"id": 1, "prefs": {"profile_public": True}}, viewer())


def test_hidden_profile_only_for_self_and_admins():
    hidden = {"id": 1, "prefs": {"profile_public": False}}
    assert not visible_to(hidden, viewer())
    assert not visible_to(hidden, None)
    assert visible_to(hidden, viewer(uid=1))
    assert visible_to(hidden, viewer(role="admin")) and visible_to(hidden, viewer(role="superadmin"))
