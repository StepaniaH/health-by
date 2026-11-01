"""指标层单测：SRI、作息分布、睡眠债、余弦节律、ACWR、置信区间。"""

from __future__ import annotations

from datetime import date, timedelta

from health_by.metrics import (
    acwr,
    cosinor,
    fisher_ci,
    hourly_activity,
    sleep_bins_by_date,
    sleep_debt,
    sleep_timing,
    sri,
    strength_label,
    swc,
)


def _sleep_day(day: str, start_hour: int, end_hour: int) -> dict:
    start = f"{day}T{start_hour:02d}:00:00+08:00"
    end_date = date.fromisoformat(day) + (timedelta(days=1) if end_hour <= start_hour else timedelta())
    end = f"{end_date.isoformat()}T{end_hour:02d}:00:00+08:00"
    return {
        "date": day,
        "sleep": {
            "start_time": start,
            "end_time": end,
            "total_minutes": (end_hour - start_hour) % 24 * 60,
            "stage_timeline": [{"start": start, "end": end, "stage": "light"}],
        },
    }


def test_sri_is_100_for_identical_schedules():
    days = [_sleep_day(f"2026-10-{day:02d}", 1, 7) for day in range(1, 9)]
    result = sri(days)
    assert result["value"] == 100.0
    assert result["pairs"] == 7


def test_sri_drops_for_alternating_schedules():
    days = []
    for index in range(10):
        if index % 2 == 0:
            days.append(_sleep_day(f"2026-10-{index + 1:02d}", 1, 7))
        else:
            days.append(_sleep_day(f"2026-10-{index + 1:02d}", 4, 10))
    assert sri(days)["value"] < 80


def test_sleep_bins_split_across_midnight():
    bins = sleep_bins_by_date([_sleep_day("2026-10-01", 23, 7)])
    assert len(bins["2026-10-01"]) == 96
    assert sum(bins["2026-10-01"]) > 0
    assert sum(bins["2026-10-02"]) > 0


def test_social_jetlag_detects_weekend_shift():
    days = []
    start = date(2026, 9, 28)  # 周一
    for index in range(14):
        current = start + timedelta(days=index)
        weekend = current.weekday() >= 5
        days.append(_sleep_day(current.isoformat(), 1 if weekend else 23, 9 if weekend else 7))
    timing = sleep_timing(days)
    assert timing["social_jetlag_min"] is not None
    assert timing["social_jetlag_min"] >= 60


def test_sleep_debt_accumulates_only_recorded_nights():
    days = [
        {"date": "2026-10-01", "sleep": {"total_minutes": 360}},
        {"date": "2026-10-02", "sleep": {"total_minutes": 420}},
        {"date": "2026-10-03", "sleep": {}},
    ]
    debt = sleep_debt(days, need_minutes=480, window=7)
    assert debt["minutes"] == 180
    assert debt["nights"] == 2


def test_cosinor_recovers_peak_hour():
    hourly: list[float | None] = [20.0] * 24
    for hour in range(9, 16):
        hourly[hour] = 100.0 * (1 + (hour - 15) / 6)
    result = cosinor(hourly)
    assert 13 <= result["acrophase_hour"] <= 17
    assert result["amplitude"] > 0


def test_hourly_activity_averages_by_hour():
    days = [
        {"activity": {"steps_intraday": [{"time": "09:00", "value": 100}]}},
        {"activity": {"steps_intraday": [{"time": "09:30", "value": 300}]}},
    ]
    hourly = hourly_activity(days)
    assert hourly[9] == 200
    assert hourly[10] is None


def test_acwr_requires_full_chronic_window():
    values: list[float | None] = [100.0] * 20
    assert acwr(values)["ratio"] is None
    values = [100.0] * 28
    values[-7:] = [150.0] * 7
    result = acwr(values)
    assert result["ratio"] == 1.33


def test_fisher_ci_and_strength():
    low, high = fisher_ci(0.5, 30)
    assert low is not None and high is not None
    assert low < 0.5 < high
    assert strength_label(0.7) == "强"
    assert strength_label(0.1) == "很弱"
    assert swc([10, 10, 10, 10]) == 0.0
