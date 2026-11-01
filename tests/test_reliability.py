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
