"""独立手写出口契约，不从实现白名单派生期望值。"""

import json
import re

from health_by.ai.service import build_payload, build_window_payload, payload_audit


# 此表由审计者核对；不得 import 实现中的字段常量来生成。
EXPECTED = {
    "date": None, "weekday": None, "rolling_window_days": None,
    "window": "days days_with_data days_with_archive missing_days max_missing_streak date_range",
    "day": {
        "activity": "steps_today distance_km_today calories_out_today floors_today total_active_minutes_today light_active_minutes_today moderate_active_minutes_today vigorous_active_minutes_today zone_minutes_today sedentary_minutes_today",
        "heart": "resting_hr_today resting_hr_7d hrv_ms_today hrv_ms_7d spo2_today spo2_7d spo2_min_today spo2_max_today breathing_rate_today breathing_rate_7d vo2max_today hr_min_today hr_max_today hr_avg_today skin_temp_delta_c_today skin_temp_delta_c_7d skin_temp_nightly_c_today skin_temp_baseline_c_today skin_temp_stddev_30d_c_today core_temp_c_today non_rem_hr_today hrv_deep_rmssd_ms_today hrv_entropy_today",
        "sleep": {"total_minutes_today": None, "total_minutes_7d": None, "efficiency_today": None, "minutes_awake_today": None,
                  "minutes_to_fall_asleep_today": None, "stages": "deep_today light_today rem_today wake_today"},
        "body": "weight_kg_today body_fat_pct_today water_ml_today calories_in_today",
        "naps_count_today": None, "naps_minutes_today": None, "alerts": "irregular_rhythm_alerts_today",
    },
    "goals": "steps sleep_minutes",
    "statistics": {
        "window_days": None, "n_days": None,
        "comparisons": {
            "steps": "current_today baseline difference_today percent_change_today sample_count window_days min_samples",
            "sleep_minutes": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
            "resting_hr": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
            "hrv_ms": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
            "breathing_rate": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
            "spo2": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
            "skin_temp_delta": "current_today current_7d baseline difference_today difference_7d percent_change_today percent_change_7d sample_count window_days min_samples",
        },
        "period_deltas": "steps_7d sleep_7d",
        "goal_progress": {"steps": "ratio_today remaining_today", "sleep_minutes": "ratio_today remaining_today"},
        "bands_outside": {
            "steps": "points outside percent", "sleep_minutes": "points outside percent",
            "hrv_ms": "points outside percent", "resting_hr": "points outside percent", "active_minutes": "points outside percent",
        },
        "recovery": {"need_minutes": None, "hrv_delta_pct_today": None, "rhr_delta_today": None,
                     "sleep_debt": "minutes nights window avg_deficit"},
        "load": "steps_avg_7 steps_avg_28 steps_ratio active_avg_7 active_avg_28 active_ratio who_weighted_minutes who_weekly_minutes who_pct window_days",
        "rhythm": "sri sri_pairs midpoint_sd_min bed_sd_min wake_sd_min social_jetlag_min n_nights rhythm_days baseline_days",
        "coverage": {
            "window_days": None, "days_with_archive": None, "partial_days": None,
            "per_metric_days": "sleep_minutes sleep_efficiency resting_hr hrv_ms steps active_minutes sedentary_minutes",
            "excluded_out_of_range": "sleep_minutes sleep_efficiency resting_hr hrv_ms spo2 breathing_rate weight_kg body_fat_pct",
        },
        "correlations": {
            "sleep_minutes__resting_hr": "x y offset_days r n ci_low ci_high",
            "sleep_minutes__hrv_ms": "x y offset_days r n ci_low ci_high",
            "deep_minutes__resting_hr": "x y offset_days r n ci_low ci_high",
            "sleep_efficiency__hrv_ms": "x y offset_days r n ci_low ci_high",
            "sleep_mid__resting_hr": "x y offset_days r n ci_low ci_high",
            "sleep_mid__hrv_ms": "x y offset_days r n ci_low ci_high",
            "sleep_minutes__steps": "x y offset_days r n ci_low ci_high",
            "steps__sleep_minutes": "x y offset_days r n ci_low ci_high",
            "steps__deep_minutes": "x y offset_days r n ci_low ci_high",
            "steps__resting_hr": "x y offset_days r n ci_low ci_high",
            "active_minutes__sleep_minutes": "x y offset_days r n ci_low ci_high",
            "sedentary_minutes__resting_hr": "x y offset_days r n ci_low ci_high",
        },
    },
}


def expected_paths(schema, prefix=""):
    if isinstance(schema, str):
        schema = dict.fromkeys(schema.split())
    result = {prefix} if prefix else set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            result |= expected_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(schema, list):
        result |= expected_paths(schema[0], prefix + "[]")
    return result


def test_payload_has_exact_independently_reviewable_paths_and_no_private_values():
    private = "PRIVATE-CANARY"
    day = {"date": "2026-10-31", "profile": {"display_name": private}, "device": {"id": private},
           "activity": {"steps": 0, "steps_intraday": [private], "future_field": private},
           "heart": {"hr_intraday": [private]},
           "sleep": {"start_time": private, "end_time": private, "stage_timeline": [private],
                     "stages": {"deep": 45, "future_stage": private}},
           "body": {"weight_kg": float("nan")}, "future_field": private}
    computed = {"correlations": [{"x": "sleep_minutes", "y": "hrv_ms", "r": 0.7, "n": 20,
                                  "survives_correction": True, "future_field": private}],
                "series": [private], "timing": [private], "heatmap": [private], "facts": [private]}
    payload = build_payload(day, {"steps": 8000, "identity": private}, {}, computed, today="2026-11-01")
    audit = payload_audit(payload)
    assert set(audit["payload_keys"]) == expected_paths(EXPECTED)
    text = json.dumps(payload, allow_nan=False)
    assert private not in text and "2026-" not in text
    assert payload["date"] == "D-1" and payload["weekday"] == "周六"
    assert payload["day"]["activity"]["steps_today"] == 0
    assert payload["day"]["body"]["weight_kg_today"] is None
    assert len(audit["payload_hash"]) == 64
    assert all(set(shape) <= {"type", "length"} for shape in audit["payload_shapes"].values())
    assert payload["statistics"]["correlations"]["sleep_minutes__hrv_ms"]["r"] == 0.7


def test_unreportable_correlations_are_not_sent():
    payload = build_payload({"date": "2026-11-01"}, {}, {}, {
        "correlations": [{"x": "steps", "y": "hrv_ms", "r": 0.9, "survives_correction": False}],
    }, today="2026-11-01")
    assert all(item["r"] is None and item["n"] is None for item in payload["statistics"]["correlations"].values())


def test_total_activity_is_derived_from_archived_components_even_when_mvpa_is_missing():
    payload = build_payload({"date": "2026-11-01", "activity": {
        "active_minutes": None, "light_active_minutes": 100,
        "moderate_active_minutes": 30, "vigorous_active_minutes": 16,
    }}, {}, {}, {}, today="2026-11-01")
    assert payload["day"]["activity"]["total_active_minutes_today"] == 146


def test_sparse_exclusion_counts_mean_zero_but_missing_measurements_remain_null():
    payload = build_payload({"date": "2026-11-01"}, {}, {}, {
        "coverage": {"excluded_out_of_range": {"hrv_ms": 1}},
    }, today="2026-11-01")
    counts = payload["statistics"]["coverage"]["excluded_out_of_range"]
    assert counts["hrv_ms"] == 1
    assert counts["sleep_minutes"] == counts["resting_hr"] == 0
    assert payload["day"]["heart"]["hrv_ms_today"] is None
    assert payload["day"]["heart"]["hrv_ms_7d"] is None


def test_prompt_field_names_exist_in_the_payload():
    from health_by.ai.prompt import DAILY_SYSTEM_PROMPT, WINDOW_INPUT_NOTE

    payload = build_payload({"date": "2026-11-01"}, {}, {}, {"correlations": [{
        "x": "steps", "y": "hrv_ms", "survives_correction": True,
    }]}, today="2026-11-01")
    ranged = build_window_payload([], {}, "2026-10-01", "2026-10-31", today="2026-11-01")
    names = {part for source in (payload, ranged) for path in payload_audit(source)["payload_keys"] for part in path.split(".")}
    description = (DAILY_SYSTEM_PROMPT.split("## 输出")[0] + WINDOW_INPUT_NOTE).replace("health-by", "")
    # 单词键也检查（comparisons / coverage 等）；null 是 JSON 字面量而非字段。
    mentioned = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)*\b", description))
    assert mentioned - {"null", "today", "daily", "v2"} <= names


def test_goal_progress_adds_only_derived_values_and_only_individual_band_statistics():
    payload = build_payload({"date": "2026-11-01"}, {}, {"goal_progress": {
        "steps": {"value": 6127, "goal": 8000, "ratio": 0.766},
        "sleep_minutes": {"value": 500, "goal": 480, "ratio": 1.042},
    }}, {"bands_outside": {"steps": {"points": 28, "outside": 9, "percent": 32}},
         "band_outside": {"percent": 45}}, today="2026-11-01")
    stats = payload["statistics"]
    assert stats["goal_progress"]["steps"] == {"ratio_today": 0.766, "remaining_today": 1873}
    assert stats["goal_progress"]["sleep_minutes"]["remaining_today"] == 0
    assert stats["bands_outside"]["steps"]["percent"] == 32
    assert "band_outside" not in stats


def test_key_hash_ignores_dict_order_but_value_hash_tracks_changes():
    first = payload_audit({"a": {"b": 1}, "c": 2})
    reordered = payload_audit({"c": 2, "a": {"b": 1}})
    changed = payload_audit({"c": 3, "a": {"b": 1}})
    assert first["payload_keys_hash"] == reordered["payload_keys_hash"] == changed["payload_keys_hash"]
    assert first["payload_hash"] == reordered["payload_hash"] != changed["payload_hash"]


def test_daily_schema_keys_stay_constant_with_missing_values_and_different_approved_relations():
    empty = build_payload({"date": "2024-02-28"}, {}, {}, {}, today="2024-03-02")
    full = build_payload({"date": "2024-02-28", "activity": {"steps": 0}, "heart": {"hrv_ms": 40}}, {}, {}, {
        "correlations": [{"x": "sleep_minutes", "y": "hrv_ms", "r": 0.8, "n": 30, "survives_correction": True}],
    }, today="2024-03-02")
    assert set(payload_audit(empty)["payload_keys"]) == set(payload_audit(full)["payload_keys"]) == expected_paths(EXPECTED)
    assert payload_audit(empty)["payload_keys_hash"] == payload_audit(full)["payload_keys_hash"]
    assert empty["day"]["heart"]["hrv_ms_today"] is empty["day"]["heart"]["hrv_ms_7d"] is None
    assert full["day"]["activity"]["steps_today"] == 0


def test_dual_values_use_the_selected_calendar_window_and_same_baseline_for_both_differences():
    selected = {"date": "2024-02-28", "heart": {"hrv_ms": 40, "resting_hr": 60}, "activity": {"steps": 1000}}
    history = [
        {"date": "2024-02-21", "heart": {"hrv_ms": 900}},
        {"date": "2024-02-22", "heart": {"hrv_ms": 20, "resting_hr": 40}},
        {"date": "2024-02-29", "heart": {"hrv_ms": 800}},
    ]
    payload = build_payload(selected, {}, {"comparisons": {"hrv_ms": {"baseline": 25}}}, {},
                            history=history, today="2024-03-02")
    assert payload["date"] == "D-3"
    assert payload["day"]["heart"]["hrv_ms_today"] == 40
    assert payload["day"]["heart"]["hrv_ms_7d"] == 30
    assert payload["day"]["heart"]["resting_hr_7d"] == 50  # 均值不是 daily 模式下的显示日值。
    comparison = payload["statistics"]["comparisons"]["hrv_ms"]
    assert comparison["current_today"] == 40 and comparison["current_7d"] == 30
    assert comparison["difference_today"] == 15 and comparison["percent_change_today"] == 60
    assert comparison["difference_7d"] == 5 and comparison["percent_change_7d"] == 20
    assert all(not name.endswith("_7d") for name in payload["day"]["activity"])
    assert "current_7d" not in payload["statistics"]["comparisons"]["steps"]


def test_equal_values_missing_days_and_real_zero_keep_their_meanings():
    selected = {"date": "2024-02-28", "heart": {"hrv_ms": 40, "skin_temp_delta_c": 0}}
    payload = build_payload(selected, {}, {}, {}, history=[
        {"date": "2024-02-22", "heart": {"hrv_ms": 40, "skin_temp_delta_c": 0}},
        {"date": "2024-02-27", "heart": {"hrv_ms": None}},
    ])
    assert payload["day"]["heart"]["hrv_ms_today"] == payload["day"]["heart"]["hrv_ms_7d"] == 40
    assert payload["day"]["heart"]["skin_temp_delta_c_today"] == payload["day"]["heart"]["skin_temp_delta_c_7d"] == 0
    assert payload["day"]["heart"]["spo2_today"] is payload["day"]["heart"]["spo2_7d"] is None


def test_window_v1_full_payload_and_path_hash_match_the_pre_change_synthetic_fixture():
    from test_ai_windows import sparse_days

    payload = build_window_payload(sparse_days(), {"steps": 200, "sleep_minutes": 480},
                                   "2026-10-01", "2026-10-07", today="2026-10-07")
    audit = payload_audit(payload)
    assert audit["payload_hash"] == "094596697a22b2719138ae615767ea0d83d51160e13e1f51992f1aaeefab68dd"
    assert audit["payload_keys_hash"] == "8ec19b27b31fd663272dd828b3bcbc10845132f4ac40756602f8d638143cd70e"
    import hashlib
    from health_by.ai import prompt
    messages_hash = hashlib.sha256(json.dumps(prompt.build_messages(payload), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert messages_hash == "a813f3f2a46d88e281f6c83c407edb3021b1cc3a079a407aa82016d8c24ba631"
    assert prompt.version_for_window("2026-10-01", "2026-10-07") == "v4-window-1"


def test_seven_day_label_is_supported_even_when_every_measurement_is_missing():
    from health_by.ai.validation import validate_output

    payload = build_payload({"date": "2024-02-28"}, {}, {}, {}, today="2024-02-28")
    text = json.dumps({"phenomenon": "HRV 7 日均值为 null。", "causes": "当日 HRV 没有记录。", "advice": None})
    _, validation = validate_output(text, payload)
    assert validation["missed"] == [] and validation["state"] in {"ok", "warn"}


def test_today_suffix_preserves_existing_sleep_remaining_duration_conversion():
    from health_by.ai.validation import validate_output

    payload = build_payload({"date": "2024-02-28", "sleep": {"total_minutes": 400}},
                            {"sleep_minutes": 480},
                            {"goal_progress": {"sleep_minutes": {"value": 400, "goal": 480}}}, {},
                            today="2024-02-28")
    text = json.dumps({"phenomenon": "当日睡眠还差 1 小时 20 分钟。", "causes": "当日睡眠时长有记录。", "advice": None})
    _, validation = validate_output(text, payload)
    assert validation["missed"] == [] and validation["state"] in {"ok", "warn"}


def test_daily_and_window_payloads_select_their_own_system_prompt():
    """单日与窗口必须各用各的系统提示词；判据是窗口信息，不是版本字段。"""
    from health_by.ai import prompt as prompt_module

    daily = build_payload({"date": "2024-02-28"}, {}, {}, {}, today="2024-03-02")
    window = build_window_payload(
        [{"date": "2024-02-28", "activity": {"steps": 1000}}], {}, "2024-02-26", "2024-02-28", today="2024-03-02"
    )
    assert prompt_module.build_messages(daily)[0]["content"] == prompt_module.DAILY_SYSTEM_PROMPT
    assert prompt_module.build_messages(window)[0]["content"] == prompt_module.SYSTEM_PROMPT


def test_daily_system_prompt_still_requires_naming_the_time_scope():
    """提示词内容本身也要守住：字段说明与"写清口径"的要求都不能少。"""
    from health_by.ai.prompt import DAILY_SYSTEM_PROMPT

    assert "_today" in DAILY_SYSTEM_PROMPT
    assert "当日" in DAILY_SYSTEM_PROMPT
    assert "7 日均值" in DAILY_SYSTEM_PROMPT


def test_payload_carries_no_version_leaf():
    """版本号只进数据库与缓存键，不作为模型输入。"""
    payload = build_payload({"date": "2024-02-28"}, {}, {}, {}, today="2024-03-02")
    leaves = set(payload_audit(payload)["payload_keys"])
    assert not [key for key in leaves if key.rsplit(".", 1)[-1].endswith("_version")]
