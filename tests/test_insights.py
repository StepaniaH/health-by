"""关联与节律层测试：相关性、基线带、作息、周内节律、热图（纯合成数据）。"""

from __future__ import annotations

from datetime import date as date_type

import pytest

from health_by.insights import build_insights, pearson


def make_day(date: str, **values) -> dict:
    day: dict = {
        "date": date,
        "activity": {"steps": values.get("steps"), "steps_intraday": values.get("intraday") or []},
        "heart": {"resting_hr": values.get("resting_hr"), "hrv_ms": values.get("hrv")},
        "sleep": None,
        "body": {"weight_kg": values.get("weight")},
    }
    if values.get("sleep_minutes") is not None:
        day["sleep"] = {
            "total_minutes": values["sleep_minutes"],
            "efficiency": values.get("efficiency"),
            "stages": {"deep": values.get("deep")},
            "start_time": values.get("bed"),
            "end_time": values.get("wake"),
        }
    return day


def _date(index: int) -> str:
    return f"2026-06-{1 + index:02d}"


def test_pearson_edge_cases():
    assert pearson([1, 2, 3], [2, 4, 6]) == 1
    assert pearson([1, 2], [1, 2]) is None  # 样本不足
    assert pearson([1, 1, 1], [1, 2, 3]) is None  # 方差为零


def test_empty_input_is_graceful():
    payload = build_insights([], {})
    assert payload["n_days"] == 0
    assert payload["correlations"] == []
    assert payload["from"] is None
    assert all(cell is None for row in payload["heatmap"] for cell in row)
    assert payload["timing"] == []


def test_correlation_detects_relationship_and_ranks():
    days = []
    for index in range(30):
        days.append(
            make_day(
                _date(index),
                sleep_minutes=360 + index * 4,  # 递增
                resting_hr=72 - index,  # 递减 → 强负相关
                hrv=40 + index * 0.8,
            )
        )
    payload = build_insights(days, {})
    found = {(c["x"], c["y"]): c for c in payload["correlations"]}
    sleep_rhr = found.get(("sleep_minutes", "resting_hr"))
    assert sleep_rhr is not None
    assert sleep_rhr["r"] < -0.95
    assert sleep_rhr["n"] == 30
    assert sleep_rhr["slope"] < 0
    # 按 |r| 降序，最强关系排最前
    assert payload["correlations"][0]["x"] == "sleep_minutes"
    assert abs(payload["correlations"][0]["r"]) >= abs(payload["correlations"][-1]["r"])


def test_correlation_requires_minimum_samples():
    days = [make_day(_date(i), sleep_minutes=400 + i, resting_hr=60 - i) for i in range(5)]
    payload = build_insights(days, {})
    assert all(c["x"] != "sleep_minutes" or c["y"] != "resting_hr" for c in payload["correlations"])


def test_shifted_pair_uses_next_day():
    days = [make_day(_date(index), steps=5000 + index * 120) for index in range(14)]
    # 次日睡眠越来越长：步数（x）↔ 次日睡眠（y）
    for index in range(13):
        days[index + 1]["sleep"] = {
            "total_minutes": 380 + index * 4,
            "efficiency": None,
            "stages": {},
            "start_time": None,
            "end_time": None,
        }
    payload = build_insights(days, {})
    shifted = [
        c
        for c in payload["correlations"]
        if c["x"] == "steps" and c["y"] == "sleep_minutes" and c["offset_days"] == 1
    ]
    assert shifted
    assert shifted[0]["r"] > 0.9


def test_weekday_and_heatmap():
    days = [
        make_day(
            "2026-06-01",
            steps=10000,
            intraday=[{"time": "08:15", "value": 420}, {"time": "08:45", "value": 180}],
        ),
        make_day("2026-06-02", steps=8000, intraday=[{"time": "08:05", "value": 300}]),
    ]
    payload = build_insights(days, {})
    weekday_a = date_type.fromisoformat("2026-06-01").weekday()
    weekday_b = date_type.fromisoformat("2026-06-02").weekday()
    assert len(payload["weekday"]) == 7
    assert payload["weekday"][weekday_a]["steps_avg"] == 10000
    heat = payload["heatmap"]
    assert len(heat) == 7 and all(len(row) == 24 for row in heat)
    assert heat[weekday_a][8] == 600  # 420 + 180（同一天同一小时合并）
    assert heat[weekday_b][8] == 300
    assert heat[weekday_a][9] is None  # 无数据的时段


def test_timing_decimals_and_band():
    days = []
    for index in range(10):
        days.append(
            make_day(
                _date(index),
                sleep_minutes=420,
                hrv=50 + index,
                bed=f"2026-06-{1 + index:02d}T23:06:00+08:00",
                wake=f"2026-06-{2 + index:02d}T06:03:00+08:00",
            )
        )
    payload = build_insights(days, {})
    timing = payload["timing"]
    assert len(timing) == 10
    assert timing[0]["bed"] == 23.1
    assert timing[0]["wake"] == 30.05
    band = payload["bands"]["hrv_ms"]
    assert band[0]["mean"] is None  # 滚动窗口样本不足
    assert band[4]["mean"] is not None
    assert band[4]["lo"] < band[4]["mean"] < band[4]["hi"]


def test_achievements_use_goals():
    days = [make_day(_date(i), steps=8000, sleep_minutes=480) for i in range(3)]
    payload = build_insights(days, {"steps": 10000, "sleep_minutes": 480})
    assert payload["achievements"][0]["steps_ratio"] == 0.8
    assert payload["achievements"][0]["sleep_ratio"] == 1.0


def test_recovery_uses_personal_baseline_and_sleep_goal():
    days = []
    for index in range(30):
        days.append(
            make_day(
                _date(index),
                sleep_minutes=480,
                efficiency=95,
                hrv=40 + index * 0.8,
                resting_hr=70 - index * 0.6,
                bed=f"2026-06-{1 + index:02d}T23:00:00+08:00",
                wake=f"2026-06-{2 + index:02d}T07:00:00+08:00",
            )
        )
    payload = build_insights(days, {"sleep_minutes": 480, "steps": 8000})
    recovery = payload["recovery"]
    assert len(recovery["signals"]) == 4
    assert recovery["signals"][0]["state"] == "good"
    assert recovery["level"] in ("high", "good")
    assert recovery["sleep_debt"]["minutes"] == 0
    assert recovery["score"] >= 3


def test_recovery_ignores_impossible_hrv():
    days = []
    for index in range(30):
        days.append(make_day(_date(index), sleep_minutes=420, hrv=0 if index == 5 else 40))
    payload = build_insights(days, {})
    assert payload["series"][5]["hrv_ms"] is None
    assert payload["recovery"]["signals"][2]["baseline"] is not None


def test_load_uses_mvpa_and_acwr():
    days = []
    for index in range(30):
        day = make_day(_date(index), steps=6000 + index * 200)
        day["activity"].update({
            "active_minutes": 40 + index,
            "moderate_active_minutes": 20,
            "vigorous_active_minutes": 10 if index < 23 else 30,
            "sedentary_minutes": 600,
        })
        days.append(day)
    payload = build_insights(days, {})
    load = payload["load"]
    assert load["steps_ratio"] > 1.2
    assert load["mvpa_minutes_week"] >= 140
    assert load["who_pct"] is not None
    assert load["active_avg_7"] > load["active_avg_28"] - 5


@pytest.mark.parametrize(
    "moderate,vigorous,expected_pct",
    [(0, 0, 0), (30, 15, 280), (None, None, None)],
)
def test_who_percentage_distinguishes_recorded_zero_from_missing_activity(
    moderate, vigorous, expected_pct
):
    days = [
        {
            "date": _date(index),
            "activity": {
                "moderate_active_minutes": moderate,
                "vigorous_active_minutes": vigorous,
            },
        }
        for index in range(7)
    ]
    assert build_insights(days, {})["load"]["who_pct"] == expected_pct


def test_rhythm_reports_sri_and_cosinor():
    days = []
    for index in range(20):
        day = make_day(
            _date(index),
            steps=8000,
            intraday=[{"time": f"{hour:02d}:00", "value": max(0, 500 - abs(hour - 12) * 40)} for hour in range(24)],
            sleep_minutes=480,
            bed=f"2026-06-{1 + index:02d}T23:00:00+08:00",
            wake=f"2026-06-{2 + index:02d}T07:00:00+08:00",
        )
        day["sleep"]["stage_timeline"] = [
            {"start": f"2026-06-{1 + index:02d}T23:00:00+08:00", "end": f"2026-06-{2 + index:02d}T07:00:00+08:00", "stage": "light"}
        ]
        days.append(day)
    payload = build_insights(days, {})
    rhythm = payload["rhythm"]
    assert rhythm["sri"] is not None
    assert rhythm["sri_pairs"] >= 10
    assert rhythm["cosinor"]["acrophase_hour"] is not None


def test_summary_and_weekly_tables():
    days = [
        make_day(_date(index), sleep_minutes=420, efficiency=95, hrv=40, resting_hr=60, steps=9000)
        for index in range(21)
    ]
    payload = build_insights(days, {"sleep_minutes": 480, "steps": 10000})
    summary = {row["key"]: row for row in payload["summary_table"]}
    assert {"sleep_minutes", "hrv_ms", "steps"} <= set(summary)
    assert summary["sleep_minutes"]["latest"] == 420
    assert summary["sleep_minutes"]["baseline"] is not None
    weekly = payload["weekly"]
    assert weekly and weekly[-1]["days"] >= 1
    assert weekly[-1]["sleep_avg"] == 420


def test_correlation_pairs_include_the_ones_that_failed_correction():
    """画图用候选集：未通过校正的配对也必须能画。

    散点图是数据展示，不是结论——它不该因为结论过不了门槛就连图一起消失
    。而 `correlations` 只放结论、`correlations_hidden`
    只是计数，两者都给不出"该画哪两张"，所以另发 `correlations_candidates`。
    """
    days = []
    for index in range(30):
        # 故意造弱关系：随机化不大可能通过 Bonferroni 校正
        days.append(
            make_day(
                _date(index),
                sleep_minutes=400 + (index % 3) * 5,
                resting_hr=60 + (index % 5) - (index % 2),
            )
        )
    payload = build_insights(days, {})

    pairs = payload["correlations_candidates"]
    assert pairs, "算得出的配对必须出现在画图候选集里"
    assert payload["correlations"] == [], "弱关系不该被当成结论下发"

    # 未通过的也在这份候选集里，且带识别信息与样本量
    assert any(pair["survives_correction"] is False for pair in pairs)
    for pair in pairs:
        assert set(pair) == {
            "x",
            "y",
            "x_label",
            "y_label",
            "offset_days",
            "r",
            "n",
            "survives_correction",
            "slope",
        }
        assert pair["n"] >= 3
        # 标题的两个词由后端给：前端再建一张指标→中文名映射就是把同一份知识存两遍
        assert pair["x_label"] and pair["y_label"]

    # 按 |r| 降序——前端取前二即可，不需要自己再排
    magnitudes = [abs(pair["r"]) for pair in pairs if pair["r"] is not None]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_correlation_pairs_never_leak_a_conclusion():
    """候选集只给识别信息 + r + n + 画线用的 slope；解读原料（ci / strength）仍只在 `correlations` 里。"""
    days = [
        make_day(_date(i), sleep_minutes=360 + i * 4, resting_hr=72 - i)
        for i in range(30)
    ]
    payload = build_insights(days, {})
    for pair in payload["correlations_candidates"]:
        assert "ci_low" not in pair
        assert "strength" not in pair
    # 通过与未通过的配对都覆盖到同一份 series，下游才能取点
    assert payload["correlations_candidates"]


def test_correlation_candidates_have_no_duplicates_in_either_mode():
    """候选集是"全部配对"，不是"两份列表拼接"。

    `include_hidden=True` 时 `correlations` 与 `correlations_rejected` 指向同一批条目
    （调试模式把未通过的放回列表），直接拼接会让每一对出现两次——前端取 |r| 前二
    就会把同一张散点图画两遍。默认模式下两者不重叠，所以这个 bug 只在调试模式可见。
    """
    days = [
        make_day(_date(i % 28), sleep_minutes=400 + (i % 7) * 6, resting_hr=60 + (i % 5))
        for i in range(28)
    ]
    for include_hidden in (False, True):
        payload = build_insights(days, {}, include_hidden=include_hidden)
        pairs = payload["correlations_candidates"]
        identities = [(pair["x"], pair["y"], pair["offset_days"]) for pair in pairs]
        assert len(identities) == len(set(identities)), (
            f"include_hidden={include_hidden} 时候选集出现重复配对，"
            "前端取前二会画出两张一样的图"
        )
        # 候选集覆盖"本次算出来的全部配对"，与通过与否无关
        assert len(pairs) == payload["correlations_tested"] or len(pairs) <= payload[
            "correlations_tested"
        ]


def test_candidates_carry_slope_only_when_the_line_may_be_drawn():
    """回归线是绘图输入，但只对通过校正的条目画。

    把"能不能画线"写进数据形状（未通过的 slope 为 None），而不是靠前端再判一次
    `survives_correction`——约定会漏，形状不会。
    """
    days = []
    for index in range(60):
        days.append(
            make_day(
                _date(index),
                sleep_minutes=360 + index * 2,
                resting_hr=80 - index * 0.5,
                hrv=40 + index * 0.3,
            )
        )
    payload = build_insights(days, {})
    assert payload["correlations_candidates"]
    for pair in payload["correlations_candidates"]:
        if pair["survives_correction"]:
            assert pair["slope"] is not None, "通过的条目必须带上画线所需的斜率"
        else:
            assert pair["slope"] is None, "未通过的条目不该带斜率——线不该画，句子更不该写"
        # 解读句的原料仍不下发
        assert "strength" not in pair
        assert "ci_low" not in pair
