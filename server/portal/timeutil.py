from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")


def dk_now() -> datetime:
    return datetime.now(TZ)


def dk_today() -> date:
    return dk_now().date()


def in_quiet_hours(quiet: dict | None, now: datetime | None = None) -> bool:
    """quiet = {"start": "22:00", "end": "07:00"}; tomt/ens = ingen stille timer."""
    if not quiet or not quiet.get("start") or not quiet.get("end") or quiet["start"] == quiet["end"]:
        return False
    now = now or dk_now()
    cur = now.strftime("%H:%M")
    start, end = quiet["start"], quiet["end"]
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end
