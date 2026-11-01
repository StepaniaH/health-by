import json
import math

import pytest

from health_by.regime import detect_break, field_appears
from health_by.util import shift_iso


def dated(values):
    return [(shift_iso("2024-01-01", index), value) for index, value in enumerate(values)]


def test_synthetic_level_change_is_detected_at_the_first_day_of_the_later_segment():
    values = dated([35 + offset for offset in [-3, 3] * 10] + [29 + offset for offset in [-3, 3] * 10])
    result = detect_break(list(reversed(values)))
    assert result["date"] == values[20][0]
    assert result["before_mean"] == 35
    assert result["after_mean"] == 29
    assert result["delta"] == -6
    assert result["d"] == pytest.approx(6 / math.sqrt(360 / 38))
    assert result["d"] >= 0.8


def test_balanced_noise_and_short_histories_do_not_create_a_change():
    assert detect_break(dated([3, -3] * 40)) is None
    assert detect_break(dated([8] * 27)) is None
    assert detect_break(dated([8] * 40)) is None


def test_zero_within_segment_variance_still_has_a_finite_json_effect_size():
    result = detect_break(dated([10] * 20 + [20] * 20))
    assert result["date"] == "2024-01-21"
    assert result["delta"] == 10 and result["d"] >= 0.8
    json.dumps(result, allow_nan=False)


def test_field_appearance_requires_archived_missing_run_and_persistent_readings():
    pairs = dated([None] * 7 + [1, 2, 3])
    assert field_appears(list(reversed(pairs))) == "2024-01-08"
    assert field_appears(dated([None] * 7 + [1, None, 2])) is None
    assert field_appears(dated([None] * 6 + [1, 2, 3])) is None
    assert field_appears(dated([None] * 10)) is None
    assert field_appears(pairs[1:]) is None  # 未存档日不能冒充字段缺测。
    assert field_appears(dated([1] + [None] * 7 + [2, 3, 4])) is None  # 恢复不是首现。


def test_legal_zero_values_are_not_field_absence_evidence():
    assert field_appears(dated([0] * 7 + [1, 2, 3])) is None
    assert field_appears(dated([None] * 7 + [0, 0, 0])) == "2024-01-08"
    assert field_appears(dated([0] * 7 + [1, 2, 3]), zero_is_missing=True) == "2024-01-08"


def test_bad_dates_duplicates_and_segment_lengths_are_rejected():
    with pytest.raises(ValueError):
        detect_break([("20240101", 2)])
    with pytest.raises(ValueError):
        detect_break([("2024-01-01", 1), ("2024-01-01", 2)])
    with pytest.raises(ValueError):
        detect_break([], min_segment=0)


def test_statistical_change_is_not_enabled_and_does_not_shorten_baseline():
    from health_by.metrics import baseline_stats

    values = [35 + offset for offset in [-3, 3] * 10] + [29 + offset for offset in [-3, 3] * 10]
    rows = [{"date": day, "signal": value} for day, value in dated(values + [999])]
    result = baseline_stats(rows, "signal", window=40)
    assert result["valid_from"] == "2024-01-01"
    assert result["field_appearance"] is None
    assert result["break"] is None
    assert result["sample_count"] == 40
    assert result["median"] == 32 and result["spread"] == pytest.approx(4.24)
    assert result["insufficient_reason"] is None


def test_a_change_outside_the_baseline_window_does_not_change_current_values():
    from health_by.metrics import baseline_stats

    rows = [{"date": day, "signal": value} for day, value in dated([10] * 20 + [20] * 36)]
    result = baseline_stats(rows, "signal")
    assert result["median"] == 20 and result["spread"] == 0
    assert result["sample_count"] == 28 and result["break"] is None
    assert result["valid_from"] == rows[-29]["date"]
    short = baseline_stats(rows[-6:], "signal")
    assert short["median"] is short["spread"] is None
    assert short["sample_count"] == 5 and short["insufficient_reason"]


def test_only_persistent_field_appearance_shortens_baseline_and_excludes_today():
    from health_by.metrics import baseline_stats

    rows = [{"date": day, "signal": value} for day, value in dated([None] * 14 + [20] * 14 + [999])]
    result = baseline_stats(rows, "signal")
    assert result["valid_from"] == result["field_appearance"] == "2024-01-15"
    assert result["break"] is None and result["sample_count"] == 14
    assert result["median"] == 20 and result["spread"] == 0
    short = baseline_stats(rows[:17] + [{"date": "2024-01-18", "signal": 999}], "signal")
    assert short["sample_count"] == 3 and short["median"] is None and short["insufficient_reason"]


def test_skin_temperature_zero_does_not_shorten_baseline_but_missing_run_can():
    from health_by.metrics import baseline_stats

    values = [0] * 14 + [0.5] * 14 + [999]
    rows = [{"date": day, "skin_temp_delta": value} for day, value in dated(values)]
    result = baseline_stats(rows, "skin_temp_delta")
    assert result["field_appearance"] is None
    assert result["valid_from"] == "2024-01-01" and result["sample_count"] == 28
    assert result["median"] == 0.2  # 原有单小数精度，所有 28 天都参与。
    rows = [{"date": day, "skin_temp_delta": value}
            for day, value in dated([None] * 14 + [0] * 14 + [999])]
    result = baseline_stats(rows, "skin_temp_delta")
    assert result["field_appearance"] == "2024-01-15"
    assert result["sample_count"] == 14 and result["median"] == 0


def test_positive_only_field_can_use_archived_zero_run_as_absence_evidence():
    from health_by.metrics import baseline_stats

    rows = [{"date": day, "hrv_ms": value} for day, value in dated([0] * 14 + [20] * 14 + [999])]
    result = baseline_stats(rows, "hrv_ms")
    assert result["field_appearance"] == "2024-01-15"
    assert result["sample_count"] == 14 and result["median"] == 20
