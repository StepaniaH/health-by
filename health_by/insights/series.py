"""分析层序列提取、周内分布、小时热图、7 日滚动带与达成统计。"""

from __future__ import annotations

from datetime import date as date_type, timedelta
from statistics import mean, pstdev
from typing import Any

from health_by import metrics

from .common import (
    ACHIEVEMENTS_WINDOW_DAYS,
    BAND_METRICS,
    BAND_MIN_SAMPLES,
    BAND_OUTSIDE_MIN_DAYS,
    BAND_WINDOW_DAYS,
    BOUNDS,
    LEGACY_BAND_OUTSIDE_METRICS,
    WEEKDAY_WORKDAYS,
    WHO_VIGOROUS_MULTIPLIER,
    _bounded,
    _dense_window,
    _ratio,
    _sleep_bed_wake,
    _to_float,
)


def _series(days: list[dict], goals: dict) -> list[dict]:
    rows: list[dict] = []
    for day in days:
        activity = day.get("activity") or {}
        heart = day.get("heart") or {}
        sleep = day.get("sleep") or {}
        stages = sleep.get("stages") or {}
        body = day.get("body") or {}
        sleep_minutes = _bounded(_to_float(sleep.get("total_minutes")), *BOUNDS["sleep_minutes"])
        bed, wake = _sleep_bed_wake(sleep.get("start_time"), sleep.get("end_time"))
        rows.append(
            {
                "date": day.get("date"),
                "steps": _to_float(activity.get("steps")),
                "active_minutes": _to_float(activity.get("active_minutes")),
                "moderate_minutes": _to_float(activity.get("moderate_active_minutes")),
                "vigorous_minutes": _to_float(activity.get("vigorous_active_minutes")),
                "light_minutes": _to_float(activity.get("light_active_minutes")),
                # 派生值：读时算、不落库。缺 light 时为 None（不退化成等于 MVPA）。
                "total_active_minutes": metrics.total_active_minutes(
                    _to_float(activity.get("light_active_minutes")),
                    _to_float(activity.get("moderate_active_minutes")),
                    _to_float(activity.get("vigorous_active_minutes")),
                ),
                "mvpa_minutes": (
                    (_to_float(activity.get("moderate_active_minutes")) or 0.0)
                    + (_to_float(activity.get("vigorous_active_minutes")) or 0.0)
                    if activity.get("moderate_active_minutes") is not None
                    or activity.get("vigorous_active_minutes") is not None
                    else None
                ),
                # WHO 折算：1 分钟高强度按 2 分钟中等计。直接相加会系统性高估活动量。
                "mvpa_weighted": (
                    (_to_float(activity.get("moderate_active_minutes")) or 0.0)
                    + WHO_VIGOROUS_MULTIPLIER * (_to_float(activity.get("vigorous_active_minutes")) or 0.0)
                    if activity.get("moderate_active_minutes") is not None
                    or activity.get("vigorous_active_minutes") is not None
                    else None
                ),
                "sedentary_minutes": _to_float(activity.get("sedentary_minutes")),
                "distance_km": _bounded(_to_float(activity.get("distance_km")), 0, 200),
                "calories_out": _bounded(_to_float(activity.get("calories_out")), 0, 20000),
                "zone_minutes": _bounded(_to_float(activity.get("zone_minutes")), 0, 1440),
                "floors": _bounded(_to_float(activity.get("floors")), 0, 1000),
                "sleep_minutes": sleep_minutes,
                "deep_minutes": _to_float(stages.get("deep")),
                "rem_minutes": _to_float(stages.get("rem")),
                "sleep_efficiency": _bounded(_to_float(sleep.get("efficiency")), *BOUNDS["sleep_efficiency"]),
                "resting_hr": metrics.in_bounds("resting_hr", heart.get("resting_hr")),
                "hrv_ms": metrics.in_bounds("hrv_ms", heart.get("hrv_ms")),
                "spo2": metrics.in_bounds("spo2", heart.get("spo2")),
                "breathing_rate": metrics.in_bounds("breathing_rate", heart.get("breathing_rate")),
                "skin_temp_delta": _to_float(heart.get("skin_temp_delta_c")),
                "weight_kg": _bounded(_to_float(body.get("weight_kg")), *BOUNDS["weight_kg"]),
                "body_fat": _bounded(_to_float(body.get("body_fat_pct")), *BOUNDS["body_fat"]),
                "bed": bed,
                "wake": wake,
                "sleep_mid": round((bed + wake) / 2, 2) if bed is not None and wake is not None else None,
                "steps_ratio": _ratio(_to_float(activity.get("steps")), goals.get("steps")),
                "sleep_ratio": _ratio(sleep_minutes, goals.get("sleep_minutes")),
            }
        )
    return rows


def _weekday_summary(weekday_rows: list[dict]) -> dict[str, Any]:
    """工作日 vs 周末的步数日均与差值百分比。"""
    work = [row["steps_avg"] for row in weekday_rows[:WEEKDAY_WORKDAYS] if row.get("steps_avg") is not None]
    rest = [row["steps_avg"] for row in weekday_rows[WEEKDAY_WORKDAYS:] if row.get("steps_avg") is not None]
    if not work or not rest:
        return {"workday_avg": None, "weekend_avg": None, "weekend_diff_percent": None}
    work_avg = mean(work)
    rest_avg = mean(rest)
    percent = round((rest_avg - work_avg) / work_avg * 100) if work_avg else None
    return {
        "workday_avg": round(work_avg),
        "weekend_avg": round(rest_avg),
        "weekend_diff_percent": percent,
    }


def _band(series: list[dict], metric: str) -> list[dict]:
    """7 个日历日的滚动均值 ± 1 标准差（样本不足处为 null）。"""
    out: list[dict] = []
    for index, row in enumerate(series):
        window = [v for v in _dense_window(series[: index + 1], metric, BAND_WINDOW_DAYS) if v is not None]
        entry = {"date": row.get("date"), "value": row.get(metric), "mean": None, "lo": None, "hi": None}
        if row.get(metric) is not None and len(window) >= BAND_MIN_SAMPLES:
            center = mean(window)
            spread = pstdev(window)
            entry["mean"] = round(center, 2)
            entry["lo"] = round(max(0.0, center - spread), 2)
            entry["hi"] = round(center + spread, 2)
        out.append(entry)
    return out


def _band_outside_by_metric(
    series: list[dict],
    precomputed_bands: dict[str, list[dict]] | None = None,
) -> dict[str, dict[str, Any]]:
    """按指标统计"当日值落在自身 7 日均值 ±1σ 之外"的点数与比例。"""
    out: dict[str, dict[str, Any]] = {}
    for metric in BAND_METRICS:
        band = precomputed_bands[metric] if precomputed_bands is not None and metric in precomputed_bands else _band(series, metric)
        if sum(1 for entry in band if entry.get("value") is not None) < BAND_OUTSIDE_MIN_DAYS:
            continue
        points = 0
        outside = 0
        for entry in band:
            if entry.get("lo") is None or entry.get("value") is None:
                continue
            points += 1
            if entry["value"] < entry["lo"] or entry["value"] > entry["hi"]:
                outside += 1
        out[metric] = {
            "points": points,
            "outside": outside,
            "percent": round(outside / points * 100) if points else 0,
        }
    return out


def _band_outside(
    series: list[dict],
    precomputed_bands: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """四个（现有五个）基线带合并的全量口径（顶层保留原 4 项口径）。"""
    per_metric = _band_outside_by_metric(series, precomputed_bands)
    legacy = {key: value for key, value in per_metric.items() if key in LEGACY_BAND_OUTSIDE_METRICS}
    points = sum(entry["points"] for entry in legacy.values())
    outside = sum(entry["outside"] for entry in legacy.values())
    return {
        "points": points,
        "outside": outside,
        "percent": round(outside / points * 100) if points else 0,
    }


def _weekday(series: list[dict]) -> list[dict]:
    buckets: list[dict[str, list[float]]] = [
        {"steps": [], "sleep": [], "rhr": [], "hrv": []} for _ in range(7)
    ]
    for row in series:
        date_value = row.get("date")
        if not date_value:
            continue
        try:
            weekday = date_type.fromisoformat(str(date_value)).weekday()
        except ValueError:
            continue
        bucket = buckets[weekday]
        for key, name in (("steps", "steps"), ("sleep_minutes", "sleep"), ("resting_hr", "rhr"), ("hrv_ms", "hrv")):
            if row.get(key) is not None:
                bucket[name].append(float(row[key]))
    out = []
    for weekday, bucket in enumerate(buckets):
        out.append(
            {
                "weekday": weekday,
                "n": max(len(bucket["steps"]), len(bucket["sleep"])),
                "steps_avg": round(mean(bucket["steps"])) if bucket["steps"] else None,
                "sleep_avg": round(mean(bucket["sleep"])) if bucket["sleep"] else None,
                "resting_hr_avg": round(mean(bucket["rhr"]), 1) if bucket["rhr"] else None,
                "hrv_avg": round(mean(bucket["hrv"]), 1) if bucket["hrv"] else None,
            }
        )
    return out


def _heatmap(days: list[dict]) -> list[list[float | None]]:
    """周一..周日 × 0..23 时的平均步数（按已存档的分时步数）。"""
    totals = [[0.0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]
    for day in days:
        date_value = day.get("date")
        try:
            weekday = date_type.fromisoformat(str(date_value)).weekday()
        except (TypeError, ValueError):
            continue
        hourly: dict[int, float] = {}
        for point in (day.get("activity") or {}).get("steps_intraday") or []:
            time_value = str(point.get("time") or "")
            if len(time_value) < 2 or not time_value[:2].isdigit():
                continue
            hour = int(time_value[:2])
            value = _to_float(point.get("value"))
            if value is None or not (0 <= hour < 24):
                continue
            hourly[hour] = hourly.get(hour, 0.0) + value
        for hour, value in hourly.items():
            totals[weekday][hour] += value
            counts[weekday][hour] += 1
    return [
        [round(totals[weekday][hour] / counts[weekday][hour]) if counts[weekday][hour] else None for hour in range(24)]
        for weekday in range(7)
    ]


def _timing(series: list[dict]) -> list[dict]:
    return [
        {"date": row["date"], "bed": row["bed"], "wake": row["wake"]}
        for row in series
        if row.get("bed") is not None and row.get("wake") is not None
    ]


def _achievements(series: list[dict]) -> list[dict]:
    return [
        {"date": row["date"], "steps_ratio": row.get("steps_ratio"), "sleep_ratio": row.get("sleep_ratio")}
        for row in series
    ]


def _achievements_summary(
    series: list[dict],
    *,
    goals: dict | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """'近 N 个日历天里达标几天'的权威计数（N = ACHIEVEMENTS_WINDOW_DAYS），直接比较原值。"""
    dated_rows: list[tuple[date_type, dict]] = []
    for row in series:
        raw_date = row.get("date")
        if not raw_date:
            continue
        try:
            dated_rows.append((date_type.fromisoformat(str(raw_date)), row))
        except ValueError:
            continue
    anchor: date_type | None = None
    if end_date:
        try:
            anchor = date_type.fromisoformat(str(end_date))
        except ValueError:
            anchor = None
    if anchor is None and dated_rows:
        anchor = max(day for day, _ in dated_rows)
    if anchor is not None:
        window_start = anchor - timedelta(days=ACHIEVEMENTS_WINDOW_DAYS - 1)
        rows = [row for day, row in dated_rows if window_start <= day <= anchor]
    else:
        rows = series[-ACHIEVEMENTS_WINDOW_DAYS:]

    goal_map = goals or {}
    out: dict[str, Any] = {}
    for ratio_key, value_key, goal_key in (
        ("steps_ratio", "steps", "steps"),
        ("sleep_ratio", "sleep_minutes", "sleep_minutes"),
    ):
        target = _to_float(goal_map.get(goal_key))
        observed = sum(1 for row in rows if row.get(value_key) is not None)
        if target is not None and target > 0:
            achieved = sum(
                1
                for row in rows
                if row.get(value_key) is not None and float(row[value_key]) >= target
            )
        else:
            achieved = sum(1 for row in rows if (row.get(ratio_key) or 0) >= 1)
        out[ratio_key] = {
            "window_days": ACHIEVEMENTS_WINDOW_DAYS,
            "total": len(rows),
            "observed": observed,
            "missing_days": max(0, ACHIEVEMENTS_WINDOW_DAYS - observed),
            "achieved": achieved,
        }
    return out
