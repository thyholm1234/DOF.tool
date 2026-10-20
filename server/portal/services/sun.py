"""Solopgang og solnedgang (NOAA's formler, nøjagtige til omkring et minut på vores breddegrader).

Tider er minutter efter lokal midnat (Europe/Copenhagen, med sommertid), som observationernes klokkeslæt.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
_r, _d = math.radians, math.degrees


@lru_cache(maxsize=4096)
def sun_times(day: date, lat: float, lon: float) -> tuple[int, int] | None:
    """(solopgang, solnedgang) i lokale minutter efter midnat, eller None ved midnatssol/mørketid."""
    jc = (day.toordinal() + 1721425.0 - 2451545.0) / 36525  # juliansk århundrede ved middag UTC
    L = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    M = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    C = (math.sin(_r(M)) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + math.sin(_r(2 * M)) * (0.019993 - 0.000101 * jc)
         + math.sin(_r(3 * M)) * 0.000289)
    omega = 125.04 - 1934.136 * jc
    app = L + C - 0.00569 - 0.00478 * math.sin(_r(omega))
    obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60 + 0.00256 * math.cos(_r(omega))
    decl = math.asin(math.sin(_r(obliq)) * math.sin(_r(app)))
    y = math.tan(_r(obliq / 2)) ** 2
    eq_time = 4 * _d(y * math.sin(2 * _r(L)) - 2 * e * math.sin(_r(M)) + 4 * e * y * math.sin(_r(M)) * math.cos(2 * _r(L))
                     - 0.5 * y * y * math.sin(4 * _r(L)) - 1.25 * e * e * math.sin(2 * _r(M)))
    cos_ha = math.cos(_r(90.833)) / (math.cos(_r(lat)) * math.cos(decl)) - math.tan(_r(lat)) * math.tan(decl)
    if abs(cos_ha) > 1:
        return None
    ha = _d(math.acos(cos_ha))
    noon_utc = 720 - 4 * lon - eq_time
    offset = datetime(day.year, day.month, day.day, 12, tzinfo=TZ).utcoffset().total_seconds() / 60
    return round(noon_utc - 4 * ha + offset), round(noon_utc + 4 * ha + offset)
