import pytest

from health_by.reliability import assess, display_mode, icc, rolling
from health_by.util import shift_iso


def test_right_aligned_rolling_uses_available_prefix_and_does_not_fill_missing_with_zero():
    assert rolling([2, 4, 6, 8], size=3) == [2, 3, 4, 6]
    assert rolling([None, 4, None, 8], size=2) == [None, 4, 4, 8]
    assert rolling([]) == []
    with pytest.raises(ValueError):
        rolling([1], size=0)


def test_behavior_is_daily_even_with_low_reliability_and_missing_is_not_zero():
    assert display_mode("steps", 0.01) == "daily"
    assert display_mode("hrv_ms", 0.39) == "rolling7"
    assert display_mode("resting_hr", 0.4) == "daily"
    assert display_mode("hrv_ms", None) == "daily"


def week_values(groups):
    return [(shift_iso("2024-01-01", 7 * index + offset), value)
            for index, group in enumerate(groups) for offset, value in enumerate(group)]


def test_population_variance_ratio_matches_explicit_synthetic_vectors():
    high = [[center + offset for offset in [-1, 0, 1, 0, -1, 0, 1]] for center in [50, 60, 70, 80, 90]]
    low = [[50,90,10,70,30,80,20], [60,20,100,30,90,10,80], [70,30,110,40,100,20,90],
           [40,80,0,60,20,70,10], [30,70,-10,50,10,60,0]]
    assert icc(week_values(high)) == pytest.approx(0.99715099715)
    assert icc(week_values(low)) == pytest.approx(937 / 6702)  # bvar=7496/49，wvar=46120/49。
    assert icc(week_values([[60,61,59,60]] * 3)) == 0  # 方差比的定义，不冒充 ICC(1)。
    assert display_mode("hrv_ms", icc(week_values(high))) == "daily"
    assert display_mode("hrv_ms", icc(week_values(low))) == "rolling7"


def test_calendar_window_and_sparse_weeks_return_none_when_not_estimable():
    assert icc(week_values([[1, 2, 3], [4, 5, 6], [7, 8]])) is None
    assert icc(week_values([[1, 2, 3], [4, 5, 6], [7, 8, 9]])) is not None
    assert icc(week_values([[3] * 7] * 3)) is None
    assert icc(week_values([[1] * 7, [2] * 7, [3] * 7]), window_days=7) is None


def test_display_rolling_is_seven_calendar_days_not_seven_observations(monkeypatch):
    monkeypatch.setattr("health_by.reliability.icc", lambda values: 0.2)
    result = assess([("2024-02-01", {"hrv_ms": 100}), ("2024-02-27", {"hrv_ms": 20}),
                     ("2024-02-28", {"hrv_ms": 30}), ("2024-02-29", {"hrv_ms": 999})],
                    "2024-02-28", {"hrv_ms": 30})
    assert result["display_values"]["hrv_ms"] == 25
    assert result["display_mode"]["hrv_ms"] == "rolling7"


def test_iso_year_boundary_keeps_observations_in_the_same_iso_week():
    values = [("2020-12-28", 1), ("2020-12-31", 2), ("2021-01-01", 3),
              ("2021-01-04", 4), ("2021-01-05", 5), ("2021-01-06", 6),
              ("2021-01-11", 7), ("2021-01-12", 8), ("2021-01-13", 9)]
    assert icc(values) == pytest.approx(0.9)



def test_stable_within_sd_floor_and_min_rolling_samples():
    from health_by.reliability import MIN_ROLLING_SAMPLES, STABLE_WITHIN_SD, calendar_averages

    # 低噪声稳定 HRV（周内波动远小于 STABLE_WITHIN_SD）应保持 daily 展示，不被误判滚动平均
    assert MIN_ROLLING_SAMPLES == 2
    assert "hrv_ms" in STABLE_WITHIN_SD
    anchor = "2026-10-05"
    readings = []
    for week in range(4):
        for offset in range(7):
            day = shift_iso(anchor, week * 7 + offset)
            # 周间极小差异 + 周内极微波动（SD ≈ 0.4ms << 3.0ms）
            val = 42.0 + 0.4 * (offset % 2)
            readings.append((day, {"hrv_ms": val, "resting_hr": 60.0 + 0.2 * (offset % 2)}))
    assessed = assess(readings, shift_iso(anchor, 27), {"hrv_ms": 42.4, "resting_hr": 60.2})
    assert assessed["display_mode"]["hrv_ms"] == "daily"
    assert assessed["display_mode"]["resting_hr"] == "daily"

    # 近 7 日只有 1 个有效值时，calendar_averages 返回 None（至少 2 天才给滚动均值）
    single = calendar_averages([("2026-10-20", {"hrv_ms": 45.0})], "2026-10-20")
    assert single["hrv_ms"] is None
    pair = calendar_averages([("2026-10-19", {"hrv_ms": 40.0}), ("2026-10-20", {"hrv_ms": 50.0})], "2026-10-20")
    assert pair["hrv_ms"] == 45.0


def test_daily_mode_filters_zero_sentinel_out_of_display_values():
    """daily 模式下生理指标 0 占位必须过滤为 None，而行为指标 steps=0 合法保留。"""
    assessed = assess(
        [],
        "2026-10-20",
        {"resting_hr": 0, "hrv_ms": 0, "spo2": 0, "steps": 0},
    )
    assert assessed["display_mode"]["resting_hr"] == "daily"
    assert assessed["display_values"]["resting_hr"] is None
    assert assessed["display_values"]["hrv_ms"] is None
    assert assessed["display_values"]["spo2"] is None
    assert assessed["display_values"]["steps"] == 0
