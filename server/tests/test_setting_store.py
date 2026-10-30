"""setting_store: læs og skriv settings-rækker (her med en falsk session uden database)."""

from portal.models import Setting
from portal.services.setting_store import get_setting, get_setting_dict, put_setting, setting_exists


class FakeSession:
    def __init__(self):
        self.rows, self.commits = {}, 0

    async def get(self, model, key):
        assert model is Setting
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self):
        self.commits += 1


async def test_get_put_roundtrip():
    s = FakeSession()
    assert await get_setting(s, "x", 7) == 7 and not await setting_exists(s, "x")
    await put_setting(s, "x", {"a": 1})
    assert await get_setting(s, "x") == {"a": 1} and await setting_exists(s, "x") and s.commits == 1
    await put_setting(s, "x", {"a": 2}, commit=False)
    assert (await get_setting(s, "x"))["a"] == 2 and s.commits == 1


async def test_get_setting_dict_merges_defaults_and_ignores_non_dicts():
    s = FakeSession()
    assert await get_setting_dict(s, "k", {"on": True}) == {"on": True}
    await put_setting(s, "k", {"on": False, "n": 3})
    assert await get_setting_dict(s, "k", {"on": True, "m": 1}) == {"on": False, "m": 1, "n": 3}
    await put_setting(s, "k", "tekst")
    assert await get_setting_dict(s, "k", {"on": True}) == {"on": True}
