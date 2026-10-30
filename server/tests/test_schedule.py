from datetime import datetime

from portal.services import locfinds, schedule


def cfg(every, unit, at="00:00", on=True):
    return {"on": on, "every": every, "unit": unit, "at": at}


def test_slot_every_six_hours_from_offset():
    c = cfg(6, "hour", "01:20")
    assert schedule.slot(c, datetime(2026, 10, 24, 8, 0)) == datetime(2026, 10, 24, 7, 20)
    assert schedule.slot(c, datetime(2026, 10, 24, 1, 0)) == datetime(2026, 10, 23, 19, 20)


def test_slot_weekly_is_monday():
    s = schedule.slot(cfg(1, "week", "05:00"), datetime(2026, 10, 24, 12, 0))  # lørdag
    assert s == datetime(2026, 10, 19, 5, 0) and s.weekday() == 0


def test_daily_due_once_per_slot():
    c = cfg(1, "day", "02:30")
    now = datetime(2026, 10, 24, 2, 31)
    assert schedule.is_due(c, datetime(2026, 10, 23, 2, 30), now)
    assert not schedule.is_due(c, datetime(2026, 10, 24, 2, 30), now)
    assert not schedule.is_due(c, datetime(2026, 10, 23, 2, 30), datetime(2026, 10, 24, 2, 29))
    assert not schedule.is_due({**c, "on": False}, datetime(2026, 10, 23, 2, 30), now)


def test_minute_interval_runs_at_once():
    c = cfg(10, "min")
    assert schedule.is_due(c, None, datetime(2026, 10, 24, 12, 0))
    assert not schedule.is_due(c, datetime(2026, 10, 24, 11, 55), datetime(2026, 10, 24, 12, 0))


def test_clean_rejects_bad_values():
    for bad in ({"unit": "year"}, {"every": 0}, {"at": "25:00"}):
        try:
            schedule._clean("forecast", {**schedule.TASKS["forecast"][2], **bad})
        except ValueError:
            continue
        raise AssertionError(bad)
    assert schedule._clean("loc_finds", {"unit": "hour", "every": 5})["unit"] == "month"


def test_spread_over_month():
    assert locfinds.spread([3, 1, 2], 1, 28) == {"3": 1, "1": 15, "2": 28}
    assert locfinds.spread([7], 5, 9) == {"7": 5}
    assert max(locfinds.spread(list(range(40)), 1, 99).values()) == locfinds.LAST_DAY


def test_month_target_clamped():
    assert locfinds.target(31, "03:30", datetime(2026, 2, 10)) == datetime(2026, 2, 28, 3, 30)


def test_every_scheduled_task_has_a_worker_job():
    from portal import worker

    monthly = {k for k, (_, _, d) in schedule.TASKS.items() if d["unit"] == "month"}  # loc_finds: egen månedsplan
    assert set(schedule.TASKS) - monthly == set(worker.SCHEDULED)
    assert set(worker.FEATURE_OF) <= set(worker.SCHEDULED)


async def test_run_scheduled_respects_feature_flags(monkeypatch):
    from portal import worker

    ran = []

    async def job():
        ran.append(1)

    monkeypatch.setitem(worker.SCHEDULED, "netfugl_rss", job)
    await worker.run_scheduled("netfugl_rss", {"netfugl": "off"})
    await worker.run_scheduled("netfugl_rss", {"netfugl": "on", "netfugl_rss": "off"})
    assert ran == []
    await worker.run_scheduled("netfugl_rss", {"netfugl": "preview"})
    assert ran == [1]
