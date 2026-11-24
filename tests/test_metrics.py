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


def test_hourly_activity_aggregates_per_day_hour_and_cosinor_centers_bins():
    import math

    days = [
        {
            "activity": {
                "steps_intraday": [
                    *([{"time": "09:00", "value": 100}] * 10),
                    {"time": "02:00", "value": 200},
                ]
            }
        }
    ]
    hourly = hourly_activity(days)
    assert hourly[9] == 1000
    assert hourly[2] == 200

    # Peak placed at bin midpoint 15.5 (15:30) is recovered cleanly.
    cos_hourly: list[float | None] = [
        100.0 + 50.0 * math.cos(2 * math.pi * ((hour + 0.5) - 15.5) / 24)
        for hour in range(24)
    ]
    result = cosinor(cos_hourly)
    assert result["acrophase_hour"] == 15.5
    assert result["r2"] == 1.0


def test_sri_handles_midnight_crossing_without_boundary_bias():
    nights = [_sleep_day(f"2026-10-{day:02d}", 23, 7) for day in range(1, 16)]
    result = sri(nights)
    assert result["value"] == 100.0
    assert result["pairs"] == 13

    single = sri([_sleep_day("2026-10-01", 23, 7)])
    assert single["value"] is None
    assert single["pairs"] == 0


def test_sleep_timing_handles_afternoon_wakes_without_wrap():
    days = []
    start = date(2026, 9, 28)  # Monday
    for index in range(7):
        current = start + timedelta(days=index)
        if current.weekday() >= 5:
            days.append(
                {
                    "date": current.isoformat(),
                    "sleep": {
                        "start_time": f"{current.isoformat()}T03:00:00+08:00",
                        "end_time": f"{current.isoformat()}T12:30:00+08:00",
                    },
                }
            )
        else:
            days.append(_sleep_day(current.isoformat(), 23, 7))
    timing = sleep_timing(days)
    assert timing["social_jetlag_min"] == 285
    assert timing["wake_sd_min"] is not None and timing["wake_sd_min"] < 200



def test_bounds_zero_as_missing_and_full_history_field_appears():
    from health_by.metrics import baseline_stats, in_bounds, ZERO_IS_MISSING_METRICS

    assert "hrv_ms" in ZERO_IS_MISSING_METRICS
    assert in_bounds("hrv_ms", 0) is None
    assert in_bounds("hrv_ms", 42) == 42
    assert in_bounds("steps", 0) == 0
    assert in_bounds("resting_hr", 221) is None

    # 窗口前连续存在 8 天零值/越界读数（应先被过滤掉，不影响 field_appears），真正有效首现发生在基线窗口内
    start = date(2026, 9, 2)
    series = [
        {"date": (start - timedelta(days=d)).isoformat(), "hrv_ms": 0.0 if d % 2 else 500.0}
        for d in range(8, 0, -1)
    ]
    for i in range(25):
        series.append({"date": (start + timedelta(days=i)).isoformat(), "hrv_ms": 45.0 + (i % 3)})
    target = (start + timedelta(days=25)).isoformat()
    stats = baseline_stats(series, "hrv_ms", end_date=target)
    assert stats["sample_count"] == 25
    assert stats["field_appearance"] == start.isoformat()
    assert stats["valid_from"] == start.isoformat()


def test_sleep_debt_uses_calendar_window_and_bartlett_rejects_autocorrelated_trend():
    from health_by.insights import pearson
    from health_by.metrics import correlation_is_significant, effective_sample_size, sleep_debt

    # 隔天缺失仍按日历 14 天切片，而不是取最近 14 条记录
    days = [
        {"date": "2026-10-01", "sleep": {"total_minutes": 300}},
        {"date": "2026-10-10", "sleep": {"total_minutes": 420}},
        {"date": "2026-10-20", "sleep": {"total_minutes": 420}},
    ]
    debt = sleep_debt(days, 480, window=14)
    # 截止 2026-10-20 的 14 天窗口为 2026-10-07..10-20，仅含 10-10 与 10-20 两晚（各少 60 分钟）
    assert debt["nights"] == 2 and debt["minutes"] == 120

    # 高度自相关的线性趋势：Bartlett 校正显著压低 n_eff，避免伪显著
    dates = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    xs = [float(i) for i in range(1, 31)]
    ys = [100.0 + 2.0 * i for i in range(1, 31)]
    r = pearson(xs, ys)
    assert abs(r - 1.0) < 1e-9
    n_eff = effective_sample_size(dates, xs, ys)
    assert n_eff < 10
    assert correlation_is_significant(0.6, len(xs), alpha=0.05 / 12) is True
    assert correlation_is_significant(0.6, n_eff, alpha=0.05 / 12) is False


def test_sri_invariant_to_segment_splitting_and_sleep_timing_handles_18h_boundary():
    """同一晚睡眠拆成多段不改变 SRI；17:55 与 18:05 入睡的环形统计不产生 1430 分钟跳变。"""
    # 1. SRI 拆段不变性：连续 6 晚 23:00-07:00，单段 vs 每晚拆成 3 段
    single_seg_days = []
    split_seg_days = []
    for i in range(6):
        wake_day = date(2026, 6, 2) + timedelta(days=i)
        bed_day = wake_day - timedelta(days=1)
        single_seg_days.append(
            {
                "date": wake_day.isoformat(),
                "sleep": {
                    "start_time": f"{bed_day.isoformat()}T23:00:00+08:00",
                    "end_time": f"{wake_day.isoformat()}T07:00:00+08:00",
                    "stage_timeline": [
                        {"start": f"{bed_day.isoformat()}T23:00:00+08:00", "end": f"{wake_day.isoformat()}T07:00:00+08:00", "stage": "light"}
                    ],
                },
            }
        )
        split_seg_days.append(
            {
                "date": wake_day.isoformat(),
                "sleep": {
                    "start_time": f"{bed_day.isoformat()}T23:00:00+08:00",
                    "end_time": f"{wake_day.isoformat()}T07:00:00+08:00",
                    "stage_timeline": [
                        {"start": f"{bed_day.isoformat()}T23:00:00+08:00", "end": f"{wake_day.isoformat()}T01:00:00+08:00", "stage": "light"},
                        {"start": f"{wake_day.isoformat()}T01:00:00+08:00", "end": f"{wake_day.isoformat()}T04:00:00+08:00", "stage": "deep"},
                        {"start": f"{wake_day.isoformat()}T04:00:00+08:00", "end": f"{wake_day.isoformat()}T07:00:00+08:00", "stage": "rem"},
                    ],
                },
            }
        )
    sri_single = sri(single_seg_days)
    sri_split = sri(split_seg_days)
    assert sri_single["value"] == 100.0
    assert sri_split["value"] == 100.0
    assert sri_split["pairs"] == sri_single["pairs"] == 4

    # 2. 18:00 截断边界回归：工作日 17:55-01:55 vs 周末 18:05-02:05，仅差 10 分钟
    timing_days = []
    start_day = date(2026, 6, 1)  # Monday
    for i in range(14):
        wake_day = start_day + timedelta(days=i)
        bed_day = wake_day - timedelta(days=1)
        is_weekend = wake_day.weekday() >= 5
        bed_str = f"{bed_day.isoformat()}T18:05:00+08:00" if is_weekend else f"{bed_day.isoformat()}T17:55:00+08:00"
        wake_str = f"{wake_day.isoformat()}T02:05:00+08:00" if is_weekend else f"{wake_day.isoformat()}T01:55:00+08:00"
        timing_days.append({"date": wake_day.isoformat(), "sleep": {"start_time": bed_str, "end_time": wake_str}})
    timing = sleep_timing(timing_days)
    assert timing["social_jetlag_min"] == 10
    assert timing["bed_sd_min"] is not None and timing["bed_sd_min"] <= 10
