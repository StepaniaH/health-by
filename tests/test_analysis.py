"""分析层测试：基线、周期差值、头条优先级、洞察。"""

from __future__ import annotations

from datetime import timedelta

from health_by.analysis import (
    build_analysis,
    compare_with_baseline,
    period_delta,
)
from health_by.util import iso_today, parse_utc, shift_iso

DAY = "2026-06-15"


def make_trend(date: str, **metrics) -> dict:
    return {"date": date, **metrics}


def make_day(date: str = DAY, **overrides) -> dict:
    day = {
        "date": date,
        "source": "test",
        "generated_at": f"{date}T08:00:00Z",
        "profile": None,
        "device": {"last_sync_time": f"{date}T06:30:00Z"},
        "activity": {"steps": 9000, "calories_out": 2100.0, "distance_km": 6.0, "steps_intraday": []},
        "heart": {"resting_hr": 60, "hrv_ms": 55.0, "spo2": 97.5, "breathing_rate": 14.0, "skin_temp_delta_c": 0.1},
        "sleep": {"total_minutes": 480, "efficiency": 90, "stages": {"deep": 80}},
        "naps_count": None,
        "naps_minutes": None,
        "body": {},
        "workouts": [],
        "alerts": {"irregular_rhythm_alerts": 0, "ecg_readings": []},
        "coverage": {"endpoint_count": 31, "success_count": 31, "errors": []},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(day.get(key), dict):
            day[key].update(value)
        else:
            day[key] = value
    return day


def make_trends(days: int = 10, end: str | None = None, **metrics) -> list[dict]:
    end = end or shift_iso(DAY, -1)
    base = {"steps": 8000, "sleep_minutes": 460, "resting_hr": 60, "hrv_ms": 52.0, "spo2": 97.4, "breathing_rate": 14.1}
    base.update(metrics)
    return [make_trend(shift_iso(end, -(days - 1 - index)), **base) for index in range(days)]


# ---------- 基线 ----------


def test_compare_with_baseline_excludes_selected_day():
    """选中日本身不计入基线；口径为 28 天中位数（与 insights 同一份定义）。"""
    trends = make_trends(10)
    trends.append(make_trend(DAY, steps=99999))
    result = compare_with_baseline(trends, DAY, 10000, lambda p: p.get("steps"))
    assert result["baseline"] == 8000
    assert result["sample_count"] == 10
    assert result["difference"] == 2000
    assert result["percent_change"] == 25.0
    assert result["window_days"] == 28
    assert result["min_samples"] == 7


def test_compare_with_baseline_refuses_to_guess_from_too_few_samples():
    """样本不足 7 天时不给基线——两个点算出来的"基线"就是噪音。

    这条是统一口径后的行为变化：以前前 7 天均值无下限，2 个点也报基线。
    """
    trends = [
        make_trend("2026-10-10", steps=None),
        make_trend("2026-10-11", steps=6000),
        make_trend("2026-10-12", steps=None),
        make_trend("2026-10-13", steps=8000),
    ]
    result = compare_with_baseline(trends, "2026-10-14", 10000, lambda p: p.get("steps"))
    assert result["baseline"] is None
    assert result["sample_count"] == 2
    assert result["difference"] is None


def test_compare_with_baseline_zero_baseline_no_percent():
    """基线为 0 时不算百分比（除零），但仍给差值。需要够 7 个样本才算得出基线。"""
    trends = make_trends(8, steps=0)
    result = compare_with_baseline(trends, DAY, 100, lambda p: p.get("steps"))
    assert result["baseline"] == 0
    assert result["percent_change"] is None
    assert result["difference"] == 100


def test_period_delta_needs_min_samples():
    assert period_delta([1, None, None, None, None, None, None, None, None, None, None, None, None, None]) is None
    values = [100] * 7 + [120] * 7
    assert period_delta(values) == 20.0


# ---------- build_analysis ----------


def test_analysis_default_headline_and_goals():
    day = make_day(activity={"steps": 7000})
    result = build_analysis(day, make_trends(), {"steps": 8000, "sleep_minutes": 480})
    assert result["headline"]["tone"] == "ok"
    assert "基线" in result["headline"]["title"]
    assert result["goal_progress"]["steps"]["ratio"] == 0.875
    assert result["goal_progress"]["sleep_minutes"]["ratio"] == 1.0
    assert result["sync"]["is_partial"] is False
    assert "headline" in result


def test_analysis_alert_headline_wins():
    day = make_day(alerts={"irregular_rhythm_alerts": 2})
    result = build_analysis(day, make_trends(), {"steps": 8000})
    assert result["headline"]["tone"] == "alert"
    assert "不规则心律" in result["headline"]["title"]


def test_analysis_stale_headline_for_today():
    today = iso_today()
    day = make_day(date=today, device={"last_sync_time": f"{today}T06:00:00Z"})
    now = parse_utc(f"{today}T06:00:00Z") + timedelta(hours=7)
    result = build_analysis(day, [], {}, now=now)
    assert result["headline"]["tone"] == "warn"
    assert "同步" in result["headline"]["title"]


def test_analysis_partial_coverage_headline():
    day = make_day(coverage={"endpoint_count": 31, "success_count": 26, "errors": [{"key": "ecgRaw"}]})
    result = build_analysis(day, make_trends(), {})
    assert result["headline"]["tone"] == "warn"
    assert "部分测量可能缺失" in result["headline"]["title"]
    assert result["sync"]["succeeded"] == 26


def test_analysis_sleep_debt_headline():
    day = make_day(sleep={"total_minutes": 380})
    result = build_analysis(day, make_trends(), {"sleep_minutes": 480})
    assert "睡眠" in result["headline"]["title"]
    assert "少" in result["headline"]["body"]


def test_analysis_rhr_elevated_headline():
    day = make_day(heart={"resting_hr": 66}, sleep={"total_minutes": 480})
    result = build_analysis(day, make_trends(), {"sleep_minutes": 480, "steps": 8000})
    assert "静息心率" in result["headline"]["title"]


def test_analysis_steps_goal_reached_headline():
    day = make_day(activity={"steps": 12000})
    result = build_analysis(day, make_trends(), {"steps": 8000, "sleep_minutes": 480})
    assert "步数目标已达成" in result["headline"]["title"]


def test_analysis_insights_include_hrv_and_naps():
    day = make_day(naps_count=1, naps_minutes=25)
    result = build_analysis(day, make_trends(), {"steps": 8000})
    ids = {item["id"] for item in result["insights"]}
    assert "hrv" in ids
    assert "naps" in ids
    assert "steps_period" in ids


def test_analysis_without_sleep_section():
    day = make_day(sleep=None)
    result = build_analysis(day, make_trends(), {"sleep_minutes": 480})
    assert result["comparisons"]["sleep_minutes"]["current"] is None


def test_baseline_is_identical_across_analysis_and_insights():
    """同一个「基线」只有一份定义：两个模块在同一份数据上必须算出同一个数。

    这条是防止"同一个词两个答案"复发的唯一办法——由不掌握实现的一侧来断言
    （tests/ 属 keeper），而不是让实现方自己等比。

    注意喂进去的形状要**真实**：insights 吃的是嵌套日快照（get_day_window 那种），
    analysis 吃的是扁平趋势行（get_trends 那种）。两边同源、形状不同，才测得出真问题。
    """
    from health_by import metrics
    from health_by.insights import build_insights

    goals = {"steps": 8000, "sleep_minutes": 480}
    days = []
    trends = []
    for index in range(21):
        date = shift_iso(DAY, -(20 - index))
        rhr = 58 + (index % 3)
        hrv = 50.0 + (index % 4)
        sleep_minutes = 460 + (index % 5) * 10
        steps = 8000 + (index % 7) * 100
        days.append(
            make_day(
                date,
                activity={"steps": steps},
                heart={"resting_hr": rhr, "hrv_ms": hrv},
                sleep={"total_minutes": sleep_minutes, "efficiency": 90},
            )
        )
        trends.append(
            make_trend(date, steps=steps, resting_hr=rhr, hrv_ms=hrv, sleep_minutes=sleep_minutes)
        )

    analysis = build_analysis(days[-1], trends, goals)
    insights = build_insights(days, goals, end_date=DAY, window_days=28)

    # 只比两边都真的存在的字段：analysis.comparisons 没有 sleep_efficiency，
    # insights.recovery 没有 sleep_minutes——不硬凑不存在的配对。
    pairs = (("resting_hr", "rhr_baseline"), ("hrv_ms", "hrv_baseline"))
    for comparison_key, recovery_key in pairs:
        from_analysis = analysis["comparisons"][comparison_key]["baseline"]
        from_insights = insights["recovery"].get(recovery_key)
        assert from_analysis == from_insights, (
            f"{comparison_key} 基线不一致：analysis={from_analysis} insights={from_insights}"
        )

    # 再对一遍共享定义本身，防止两边一起错成同一个值
    from_metrics = metrics.baseline_stats(trends, "resting_hr")
    assert analysis["comparisons"]["resting_hr"]["baseline"] == from_metrics["median"]
    assert analysis["comparisons"]["resting_hr"]["sample_count"] == from_metrics["sample_count"]
    assert analysis["comparisons"]["resting_hr"]["window_days"] == metrics.BASELINE_WINDOW_DAYS


def test_lifted_frontend_rationales_match_the_old_frontend_math():
    """上移的那两个口径，必须和前端原来的算法**逐字节相同**。

    否则 @forge 把前端切到新字段的那一刻，屏幕上的数字会悄悄变一次——
    用户看到"同一个页面刷新后数字不一样"，而没人改过数据。
    这里用 Python 复刻前端原公式，与后端字段逐一比对。
    """
    from statistics import mean

    from health_by.insights import build_insights

    days = []
    for index in range(40):
        date = shift_iso(DAY, -(39 - index))
        days.append(
            make_day(
                date,
                activity={"steps": 8000 + (index % 9) * 250, "active_minutes": 60 + index % 7},
                heart={"resting_hr": 58 + index % 4, "hrv_ms": 48.0 + index % 5},
                sleep={"total_minutes": 450 + (index % 6) * 12, "efficiency": 88 + index % 5},
            )
        )
    insights = build_insights(days, {"steps": 8000, "sleep_minutes": 480}, end_date=DAY, window_days=28)

    # 前端 dashboard.js: addBand 累加 + outsideShare
    points = outside = 0
    for metric in ("resting_hr", "hrv_ms", "sleep_minutes", "steps"):
        band = insights["bands"].get(metric) or []
        if sum(1 for entry in band if entry.get("value") is not None) < 7:
            continue
        for entry in band:
            if entry.get("lo") is None or entry.get("value") is None:
                continue
            points += 1
            if entry["value"] < entry["lo"] or entry["value"] > entry["hi"]:
                outside += 1
    expected_band = {
        "points": points,
        "outside": outside,
        "percent": round(outside / points * 100) if points else 0,
    }
    assert insights["band_outside"] == expected_band

    # 前端 renderWeekly: work/rest 日均与差值百分比
    rows = insights["weekday"]
    work = [row["steps_avg"] for row in rows[:5] if row.get("steps_avg") is not None]
    rest = [row["steps_avg"] for row in rows[5:] if row.get("steps_avg") is not None]
    if work and rest:
        work_avg, rest_avg = mean(work), mean(rest)
        expected_weekday = {
            "workday_avg": round(work_avg),
            "weekend_avg": round(rest_avg),
            "weekend_diff_percent": round((rest_avg - work_avg) / work_avg * 100) if work_avg else None,
        }
    else:
        expected_weekday = {"workday_avg": None, "weekend_avg": None, "weekend_diff_percent": None}
    assert insights["weekday_summary"] == expected_weekday


def test_per_metric_band_outside_matches_its_own_band():
    """按指标的带外统计必须与同 key 的 bands 严格同源。

    - `points` 等于该指标 band 里"值、lo、hi 都非空"的条目数；
    - 顶层全量 `band_outside` 保持原有 4 项口径，不因新增指标而悄悄变数
      （否则屏幕上那个百分比会在数据没变的情况下跳一次）。
    """
    from health_by.insights import build_insights

    days = []
    for index in range(30):
        date = shift_iso(DAY, -(29 - index))
        days.append(
            make_day(
                date,
                activity={"steps": 8000 + (index % 9) * 250, "active_minutes": 60 + index % 7},
                heart={"resting_hr": 58 + index % 4, "hrv_ms": 48.0 + index % 5},
                sleep={"total_minutes": 450 + (index % 6) * 12, "efficiency": 88 + index % 5},
            )
        )
    insights = build_insights(days, {"steps": 8000}, end_date=DAY, window_days=30)

    per_metric = insights["bands_outside"]
    assert per_metric, "至少要有一个指标够 7 天才能统计"

    for key, stats in per_metric.items():
        band = insights["bands"][key]
        usable = [
            entry for entry in band
            if entry.get("value") is not None and entry.get("lo") is not None
        ]
        assert stats["points"] == len(usable), f"{key} 的点数与自身 band 不一致"
        outside = sum(1 for e in usable if e["value"] < e["lo"] or e["value"] > e["hi"])
        assert stats["outside"] == outside, f"{key} 的带外计数与自身 band 不一致"
        assert stats["percent"] == (round(outside / len(usable) * 100) if usable else 0)

    # 顶层全量仍是原来的 4 项口径
    legacy = {k: v for k, v in per_metric.items() if k in ("resting_hr", "hrv_ms", "sleep_minutes", "steps")}
    assert insights["band_outside"]["points"] == sum(v["points"] for v in legacy.values())
    assert insights["band_outside"]["outside"] == sum(v["outside"] for v in legacy.values())


def test_windows_echoes_every_window_constant():
    """`insights.windows` 必须是所有窗口常量的唯一回显点。

    双向断言，缺一不可：
    1. **值**对得上对应常量 —— 改了常量却没回声会红；
    2. **键集合**对得上预期集合 —— 新增常量却忘了回声也会红（这条才是防"下一次"的）。
    """
    from health_by import metrics
    from health_by.insights import (
        ACHIEVEMENTS_WINDOW_DAYS,
        BAND_WINDOW_DAYS,
        RHYTHM_WINDOW_DAYS,
        SHORT_WINDOW_DAYS,
        SUMMARY_TREND_DAYS,
        build_insights,
    )

    days = [make_day(shift_iso(DAY, -index)) for index in range(3)]
    insights = build_insights(days, {}, end_date=DAY, window_days=90)

    expected = {
        "band": BAND_WINDOW_DAYS,
        "short": SHORT_WINDOW_DAYS,
        "baseline": metrics.BASELINE_WINDOW_DAYS,
        "summary_trend": SUMMARY_TREND_DAYS,
        "achievements": ACHIEVEMENTS_WINDOW_DAYS,
        "rhythm": RHYTHM_WINDOW_DAYS,
    }
    # ① 键集合：新增窗口常量必须同时回显，否则前端又会自己写死一份
    assert set(insights["windows"]) == set(expected), (
        "windows 的键变了：新增常量请同步回显，否则前端只能再次硬编码"
    )
    # ② 值：常量改了这里必须跟着变
    assert insights["windows"] == expected
    # 请求窗口只叫 window_days，不许在 windows 里再起一个名字
    assert "request" not in insights["windows"], (
        "windows.request 是 window_days 的第二个名字；同一个值不许两个键名"
    )
    assert build_insights(days, {}, end_date=DAY, window_days=30)["window_days"] == 30


def test_achievements_summary_replaces_the_front_end_slice():
    """达成统计的窗口与计数都由后端给，且与前端原来的 slice(-28) 口径一致。"""
    from health_by.insights import ACHIEVEMENTS_WINDOW_DAYS, build_insights

    days = [
        make_day(shift_iso(DAY, -(39 - index)), activity={"steps": 9000 if index % 2 else 1000})
        for index in range(40)
    ]
    insights = build_insights(days, {"steps": 8000}, end_date=DAY, window_days=90)

    summary = insights["achievements_summary"]["steps_ratio"]
    assert summary["window_days"] == ACHIEVEMENTS_WINDOW_DAYS
    assert summary["total"] == ACHIEVEMENTS_WINDOW_DAYS  # 序列够长时正好一个窗口

    # 与前端旧算法逐项对齐：取最后 28 条、数 ratio >= 1
    rows = insights["achievements"][-ACHIEVEMENTS_WINDOW_DAYS:]
    assert summary["total"] == len(rows)
    assert summary["achieved"] == sum(1 for row in rows if (row.get("steps_ratio") or 0) >= 1)

    # 序列比窗口短时，total 是实际天数（与 slice 后取 .length 一致）
    short = build_insights(days[:5], {"steps": 8000}, end_date=DAY, window_days=90)
    assert short["achievements_summary"]["steps_ratio"]["total"] == 5


def test_window_echoes_cannot_disagree():
    """同一个窗口的多个回声必须相等——它们应该是同一个变量，不是两次计算。

    散字段（`window_days` / `coverage.window_days` / `rhythm.rhythm_days` / `load.window_days`）
    现在仍然存在（前端已在用，一次性删掉会打断页面），所以用测试把它们钉成同一来源：
    一旦谁在某一处另算一个值，这里立刻红。
    """
    from health_by import metrics
    from health_by.insights import (
        ACHIEVEMENTS_WINDOW_DAYS,
        RHYTHM_WINDOW_DAYS,
        SHORT_WINDOW_DAYS,
        build_insights,
    )

    days = [make_day(shift_iso(DAY, -index)) for index in range(30)]
    insights = build_insights(days, {}, end_date=DAY, window_days=90)
    windows = insights["windows"]

    assert insights["window_days"] == insights["coverage"]["window_days"] == 90
    assert windows["rhythm"] == insights["rhythm"]["rhythm_days"] == RHYTHM_WINDOW_DAYS
    assert windows["short"] == insights["load"]["window_days"] == SHORT_WINDOW_DAYS
    assert windows["baseline"] == insights["load"]["baseline_days"] == metrics.BASELINE_WINDOW_DAYS
    assert windows["achievements"] == (
        insights["achievements_summary"]["steps_ratio"]["window_days"]
    ) == ACHIEVEMENTS_WINDOW_DAYS
