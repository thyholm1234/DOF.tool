import json
import zipfile
from datetime import date, timedelta

from portal.services import datasync, weather_archive
from portal.timeutil import dk_today


def test_tier_jobs_and_months():
    today = dk_today()
    days3 = datasync.dof_tier_jobs("days3")
    assert [p["date"] for _, p in days3] == [(today - timedelta(days=i)).isoformat() for i in (1, 2, 3)]
    assert len(datasync.dof_tier_jobs("week")) == 7
    year = datasync.dof_tier_jobs("year")
    assert all(k == "month" for k, _ in year) and len(year) == 13
    assert year[0][1] == {"year": today.year, "month": today.month}  # nyeste først
    assert datasync.months_between(date(2025, 11, 20), date(2026, 2, 1)) == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
    allm = datasync.tier_months("all", 2011)
    assert allm[0] == (2011, 1) and allm[-1] == (today.year, today.month)


def test_calls_per_day_estimate():
    plan = {k: dict(v) for k, v in datasync.DEFAULTS["dof"].items()}
    est = datasync.calls_per_day("dof", plan)
    assert est["today"] == 1440 and est["days3"] == 72 and est["week"] == 7 and est["all"] == 0
    plan["days3"]["on"] = False
    assert datasync.calls_per_day("dof", plan)["days3"] == 0
    assert datasync.interval_s({"every": 2, "unit": "hour"}) == 7200


def _feature(cell, pid, frm, val, calc="2026-01-02T00:00:00Z"):
    return json.dumps({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},
                       "properties": {"cellId": cell, "parameterId": pid, "timeResolution": "hour", "from": frm,
                                      "calculatedAt": calc, "qcStatus": "none", "value": val}})


def test_parse_member_aggregates_local_day(tmp_path):
    # 05:00 UTC = 06:00 dansk vintertid (morgen); 13:00 UTC = 14:00 (eftermiddag)
    lines = [
        _feature("10km_622_56", "mean_temp", "2026-01-15T05:00:00Z", 2.0),
        _feature("10km_622_56", "mean_temp", "2026-01-15T05:00:00Z", 3.0, calc="2026-01-03T00:00:00Z"),  # nyere beregning vinder
        _feature("10km_622_56", "mean_temp", "2026-01-15T13:00:00Z", 7.0),
        _feature("10km_622_56", "mean_wind_speed", "2026-01-15T05:00:00Z", 4.0),
        _feature("10km_622_56", "mean_wind_dir", "2026-01-15T05:00:00Z", 270.0),
        _feature("10km_622_56", "acc_precip", "2026-01-15T13:00:00Z", 1.5),
        _feature("10km_622_56", "max_wind_speed_10min", "2026-01-15T13:00:00Z", 11.0),
        _feature("10km_622_56", "snow_depth", "2026-01-15T13:00:00Z", 99.0),  # ikke ønsket – ignoreres
    ]
    zp = tmp_path / "2026-01.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("2026-01-15.txt", "\n".join(lines) + "\n")
    acc = weather_archive.parse_member(str(zp), "2026-01-15.txt")
    row = weather_archive.finalize(acc[(622056, "2026-01-15")])
    assert row["temp"] == 5.0 and row["temp_min"] == 3.0 and row["temp_max"] == 7.0
    assert row["m_temp"] == 3.0 and row["m_wind"] == 4.0 and row["m_dir"] == 270
    assert row["precip"] == 1.5 and row["gust"] == 11.0
    # sammenfletning af to døgnfiler summerer og tager min/maks
    other = {(622056, "2026-01-15"): weather_archive._new_acc()}
    other[(622056, "2026-01-15")][0:4] = [10.0, 1, 10.0, 10.0]
    weather_archive.merge(acc, other)
    merged = weather_archive.finalize(acc[(622056, "2026-01-15")])
    assert merged["temp_max"] == 10.0 and merged["temp"] == round(20.0 / 3, 2)
    assert weather_archive.cell_code("10km_617_57") == 617057
