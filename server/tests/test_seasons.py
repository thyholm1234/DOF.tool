from datetime import date

import numpy as np

from portal.services import seasons as S


def test_summer_visitor_window_has_margin():
    c = np.zeros(365)
    c[100:260] = 5
    start, end = S.stat_window(c)  # 101..260 (1-baseret) ± 5 dage; 99 % kan trimme op til en dag i den ene ende
    assert 96 <= start <= 97 and 264 <= end <= 265


def test_winter_window_wraps_over_new_year():
    c = np.zeros(365)
    c[330:] = 5
    c[:60] = 5
    start, end = S.stat_window(c)
    assert start > end and S.is_outside(start, end, 200) and not S.is_outside(start, end, 5) and not S.is_outside(start, end, 350)


def test_year_round_species_has_no_window():
    assert S.stat_window(np.ones(365)) is None


def test_rare_out_of_season_record_is_outside():
    c = np.zeros(365)
    c[100:260] = 100
    c[20] = 1  # én vinterobservation i 15 år: under 1 % af massen
    start, end = S.stat_window(c)
    assert S.is_outside(start, end, 21)


def test_doy365_ignores_leap_day():
    assert S.doy365(date(2024, 3, 1)) == S.doy365(date(2025, 3, 1)) == 60
    assert S.doy365(date(2024, 12, 31)) == 365
