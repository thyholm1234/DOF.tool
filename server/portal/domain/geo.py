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


# ---------------------------------------------------------------- UTM zone 32 (ETRS89 / EPSG:25832, Det Danske Kvadratnet)
# Krügers serier til n⁴ (Karney 2011) – under en millimeter i hele Danmark, også Bornholm, der ligger uden for zonen.
_A, _F, _K0, _E0, _LON0 = 6378137.0, 1 / 298.257222101, 0.9996, 500000.0, math.radians(9)
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_ECC = math.sqrt(_F * (2 - _F))
_ALPHA = (_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180, 13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140, 49561 * _N ** 4 / 161280)
_BETA = (_N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360, _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840, 4397 * _N ** 4 / 161280)
_DELTA = (2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3 + 116 * _N ** 4 / 45, 7 * _N ** 2 / 3 - 8 * _N ** 3 / 5 - 227 * _N ** 4 / 45,
          56 * _N ** 3 / 15 - 136 * _N ** 4 / 35, 4279 * _N ** 4 / 630)


def utm32(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) i grader → (E, N) i meter, UTM zone 32 / ETRS89."""
    phi, lam = math.radians(lat), math.radians(lon) - _LON0
    t = math.sinh(math.atanh(math.sin(phi)) - _ECC * math.atanh(_ECC * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    e = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    n = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    return _E0 + _K0 * _AA * e, _K0 * _AA * n


def utm32_inv(e: float, n: float) -> tuple[float, float]:
    """(E, N) i meter, UTM zone 32 / ETRS89 → (lat, lon) i grader."""
    xi, eta = n / (_K0 * _AA), (e - _E0) / (_K0 * _AA)
    xi_ = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    eta_ = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xi_) / math.cosh(eta_))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    return math.degrees(phi), math.degrees(_LON0 + math.atan2(math.sinh(eta_), math.cos(xi_)))


GRID_SIZES_KM = (1, 2, 5, 10)


def grid_cell(lat: float, lon: float, km_: int) -> str:
    """Kvadratet i Det Danske Kvadratnet, punktet ligger i, som DKN's kode: «10km_617_72» = nederste venstre hjørne
    N 6.170.000, E 720.000 (N og E i enheder af kvadratets side). 2 og 5 km følger samme mønster."""
    e, n = utm32(lat, lon)
    s = km_ * 1000
    return f"{km_}km_{int(n // s)}_{int(e // s)}"


def grid_ring(code: str) -> list[list[float]]:
    """Kvadratets fire hjørner som [[lat, lon], …] (mod uret fra sydvest)."""
    size, n, e = code.split("_")
    s = int(size.removesuffix("km")) * 1000
    n0, e0 = int(n) * s, int(e) * s
    return [[round(v, 6) for v in utm32_inv(x, y)] for x, y in ((e0, n0), (e0 + s, n0), (e0 + s, n0 + s), (e0, n0 + s))]
