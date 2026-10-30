"""Afstand og retning på jordkuglen (haversine) – til fund, vejrstationer og "nær mig"."""

from __future__ import annotations

import math

EARTH_DIAMETER_KM = 12742


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Afstand i km mellem (lat, lon) og (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return EARTH_DIAMETER_KM * math.asin(math.sqrt(h))


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Retning i grader (0 = nord) fra a mod b."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360


def angle_diff(a: float, b: float) -> float:
    """Mindste vinkel mellem to retninger (0–180)."""
    return abs((a - b + 180) % 360 - 180)
