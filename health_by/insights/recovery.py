"""恢复判定、运动负荷、覆盖度汇总、指标对照表、周报表与首页要点。"""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
from statistics import median
from typing import Any

from health_by import metrics
from health_by.i18n import t

from .common import (
    EFFICIENCY_LOW_DROP,
    SHORT_WINDOW_DAYS,
    SLEEP_GOOD_RATIO,
    SLEEP_LOW_RATIO,
    WHO_WEEKLY_ACTIVE_MINUTES,
    _METRIC_LABELS,
    _baseline_before,
    _days_in_calendar_window,
    _exclusions,
    _mean,
    _to_float,
    _window_values,
)


def _state_label(state: str) -> str:
    return {
        "good": t("server.insights._state_label.09dfac88c"),
        "ok": t("server.insights._state_label.296de0e31"),
        "low": t("server.insights._state_label.fd9b72d8d"),
    }.get(state, "—")


def _swc_note(baseline: float | None, swc: float | None, unit: str) -> str:
    """把判读规则摊开说，并区分三种情况。"""
    if baseline is None or swc is None:
        return t("server.insights._swc_note.db2718719")
    if swc == 0:
        return t("server.insights._swc_note.b661e5835")
    return t("server.insights._swc_note.6ed944b46", swc=swc, unit=unit)


def _recovery(series: list[dict], days: list[dict], goals: dict) -> dict[str, Any]:
    latest = series[-1] if series else {}
    need = _to_float(goals.get("sleep_minutes")) or 480.0
    sleep_minutes = latest.get("sleep_minutes")
    efficiency = latest.get("sleep_efficiency")
    hrv = latest.get("hrv_ms")
    rhr = latest.get("resting_hr")

    sleep_state = "unknown"
    if sleep_minutes is not None:
        ratio = sleep_minutes / need
        sleep_state = "good" if ratio >= SLEEP_GOOD_RATIO else ("low" if ratio < SLEEP_LOW_RATIO else "ok")

    eff_base, _ = _baseline_before(series, "sleep_efficiency")
    eff_state = "unknown"
    if efficiency is not None and eff_base is not None:
        eff_state = "good" if efficiency >= eff_base else ("low" if efficiency <= eff_base - EFFICIENCY_LOW_DROP else "ok")

    hrv_base, hrv_sd = _baseline_before(series, "hrv_ms")
    hrv_swc = round(0.5 * hrv_sd, 2) if hrv_sd is not None else None
    hrv_state = "unknown"
    if hrv is not None and hrv_base is not None and hrv_swc:
        hrv_state = "good" if hrv >= hrv_base + hrv_swc else ("low" if hrv <= hrv_base - hrv_swc else "ok")

    rhr_base, rhr_sd = _baseline_before(series, "resting_hr")
    rhr_swc = round(0.5 * rhr_sd, 2) if rhr_sd is not None else None
    rhr_state = "unknown"
    if rhr is not None and rhr_base is not None and rhr_swc:
        rhr_state = "good" if rhr <= rhr_base - rhr_swc else ("low" if rhr >= rhr_base + rhr_swc else "ok")

    signals = [
        {
            "key": "sleep",
            "label": t("server.insights._recovery.98439c55d"),
            "value": sleep_minutes,
            "unit": "min",
            "goal": need,
            "state": sleep_state,
            "state_label": _state_label(sleep_state),
            "note": t("server.insights._recovery.b8230baf0", p1=need / 60),
        },
        {
            "key": "efficiency",
            "label": t("server.insights._recovery.dc4a0545d"),
            "value": efficiency,
            "unit": "%",
            "baseline": eff_base,
            "state": eff_state,
            "state_label": _state_label(eff_state),
            "note": t("server.insights._recovery.6c355cba8", EFFICIENCY_LOW_DROP=EFFICIENCY_LOW_DROP),
        },
        {
            "key": "hrv",
            "label": "HRV",
            "value": hrv,
            "unit": "ms",
            "baseline": hrv_base,
            "state": hrv_state,
            "state_label": _state_label(hrv_state),
            "note": _swc_note(hrv_base, hrv_swc, "ms"),
        },
        {
            "key": "resting_hr",
            "label": t("server.insights._recovery.e9a48bd3e"),
            "value": rhr,
            "unit": "bpm",
            "baseline": rhr_base,
            "state": rhr_state,
            "state_label": _state_label(rhr_state),
            "note": _swc_note(rhr_base, rhr_swc, "bpm"),
        },
    ]
    available = [signal for signal in signals if signal["state"] != "unknown"]
    good = sum(1 for signal in available if signal["state"] == "good")
    low = sum(1 for signal in available if signal["state"] == "low")
    if len(available) >= 3:
        if low >= 2 or (low >= 1 and good == 0):
            level, level_label = "low", t("server.insights._recovery.fd9b72d8d")
        elif low == 0 and good >= 3:
            level, level_label = "high", t("server.insights._recovery.d56bfdd5c")
        elif low <= 1 and good >= 2:
            level, level_label = "good", t("server.insights._recovery.cfea0dce5")
        else:
            level, level_label = "ok", t("server.insights._recovery.91e25f4dd")
    else:
        level, level_label = "unknown", t("server.insights._recovery.84c6e0168")

    debt = metrics.sleep_debt(days, need_minutes=need, window=SHORT_WINDOW_DAYS)
    hrv_pct = round((hrv - hrv_base) / hrv_base * 100) if hrv is not None and hrv_base else None
    rhr_delta = round(rhr - rhr_base, 1) if rhr is not None and rhr_base is not None else None
    return {
        "date": latest.get("date"),
        "need_minutes": round(need),
        "level": level,
        "level_label": level_label,
        "score": good,
        "score_max": len(available),
        "signals": signals,
        "sleep_debt": debt,
        "hrv_baseline": hrv_base,
        "hrv_delta_pct": hrv_pct,
        "rhr_baseline": rhr_base,
        "rhr_delta": rhr_delta,
    }


def _load(series: list[dict], days: list[dict]) -> dict[str, Any]:
    steps = _window_values(series, "steps", metrics.BASELINE_WINDOW_DAYS)
    active = _window_values(series, "active_minutes", metrics.BASELINE_WINDOW_DAYS)
    mvpa = _window_values(series, "mvpa_minutes", metrics.BASELINE_WINDOW_DAYS)
    sedentary = _window_values(series, "sedentary_minutes", metrics.BASELINE_WINDOW_DAYS)
    steps_ratio = metrics.acwr(steps, acute=SHORT_WINDOW_DAYS, chronic=metrics.BASELINE_WINDOW_DAYS)
    active_ratio = metrics.acwr(active, acute=SHORT_WINDOW_DAYS, chronic=metrics.BASELINE_WINDOW_DAYS)
    mvpa_week = sum(v for v in mvpa[-SHORT_WINDOW_DAYS:] if v is not None)
    weighted_values = [
        v for v in _window_values(series, "mvpa_weighted", SHORT_WINDOW_DAYS) if v is not None
    ]
    weighted_week = sum(weighted_values)
    workout_minutes = 0.0
    for day in _days_in_calendar_window(days, SHORT_WINDOW_DAYS):
        for workout in day.get("workouts") or []:
            workout_minutes += _to_float(workout.get("duration_minutes")) or 0.0
    return {
        "steps_avg_7": _mean(steps[-SHORT_WINDOW_DAYS:]),
        "steps_avg_28": _mean(steps),
        "steps_ratio": steps_ratio["ratio"],
        "active_avg_7": _mean(active[-SHORT_WINDOW_DAYS:]),
        "active_avg_28": _mean(active),
        "active_ratio": active_ratio["ratio"],
        "mvpa_minutes_week": round(mvpa_week),
        "mvpa_avg_7": _mean(mvpa[-SHORT_WINDOW_DAYS:]),
        "who_weekly_minutes": WHO_WEEKLY_ACTIVE_MINUTES,
        "who_weighted_minutes": round(weighted_week),
        "who_pct": round(weighted_week / WHO_WEEKLY_ACTIVE_MINUTES * 100) if weighted_values else None,
        "sedentary_avg_7": _mean(sedentary[-SHORT_WINDOW_DAYS:]),
        "window_days": SHORT_WINDOW_DAYS,
        "baseline_days": metrics.BASELINE_WINDOW_DAYS,
        "workout_minutes_week": round(workout_minutes),
    }


def _coverage(series: list[dict], days: list[dict], window_days: int) -> dict[str, Any]:
    """这份结论到底是基于多少数据算出来的。"""
    metric_keys = (
        "sleep_minutes",
        "sleep_efficiency",
        "resting_hr",
        "hrv_ms",
        "steps",
        "active_minutes",
        "sedentary_minutes",
    )
    per_metric = {
        key: sum(1 for row in series if row.get(key) is not None) for key in metric_keys
    }
    partial_days = 0
    for day in days:
        coverage = day.get("coverage") or {}
        succeeded = coverage.get("success_count")
        endpoints = coverage.get("endpoint_count")
        if succeeded is None or not endpoints:
            continue
        if int(succeeded) < int(endpoints):
            partial_days += 1
    excluded = {key: value for key, value in _exclusions(days).items() if value}
    core = ("sleep_minutes", "resting_hr", "hrv_ms", "steps", "active_minutes")
    per_day_counts: list[tuple[str, int]] = []
    for row in series:
        count = sum(1 for key in core if row.get(key) is not None)
        if row.get("date"):
            per_day_counts.append((str(row["date"]), count))
    sparse: dict[str, Any] = {"dates": [], "threshold": None}
    if per_day_counts:
        counts = sorted(count for _, count in per_day_counts)
        typical = median(counts)
        threshold = max(1, int(typical) - 1)
        flagged = [day for day, count in per_day_counts if count <= threshold and typical >= 3]
        sparse = {
            "threshold": threshold,
            "typical_fields": int(typical),
            "dates": flagged[:10],
            "count": len(flagged),
        }

    present = {key: value for key, value in per_metric.items() if value > 0}
    absent = sorted(key for key, value in per_metric.items() if value == 0)
    return {
        "window_days": window_days,
        "days_with_archive": len(series),
        "excluded_out_of_range": excluded,
        "absent_metrics": absent,
        "sparse_days": sparse,
        "partial_days": partial_days,
        "per_metric_days": per_metric,
        "weakest_metric": min(present, key=lambda key: present[key]) if present else None,
        "weakest_metric_days": min(present.values()) if present else 0,
    }


def _summary_table(series: list[dict]) -> list[dict]:
    specs = (
        ("sleep_minutes", t("server.insights._summary_table.98439c55d"), "min", 0),
        ("sleep_efficiency", t("server.insights._summary_table.dc4a0545d"), "%", 0),
        ("hrv_ms", "HRV", "ms", 1),
        ("resting_hr", t("server.insights._summary_table.e9a48bd3e"), "bpm", 0),
        ("steps", t("server.insights._summary_table.2986c7ee9"), t("server.insights._summary_table.a621b7cd7"), 0),
        ("active_minutes", t("server.insights._summary_table.301220d3d"), "min", 0),
        ("sedentary_minutes", t("server.insights._summary_table.aa1b957b8"), "min", 0),
    )
    rows: list[dict] = []
    for key, label, unit, digits in specs:
        values: list[tuple[str, float]] = [
            (str(row.get("date")), float(value))
            for row in series
            for value in [row.get(key)]
            if value is not None
        ]
        if not values:
            continue
        latest_date, latest = values[-1]
        avg7 = _mean(_window_values(series, key, SHORT_WINDOW_DAYS))
        base, _ = _baseline_before(series, key)
        delta = round(latest - base, digits) if base is not None else None
        if base is None or base == 0 or delta is None:
            direction = "none"
        elif abs(delta) <= max(0.02 * abs(base), 0.5):
            direction = "flat"
        else:
            direction = "up" if delta > 0 else "down"
        rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "digits": digits,
                "latest": round(float(latest), digits),
                "latest_date": latest_date,
                "avg7": round(avg7, digits) if avg7 is not None else None,
                "baseline": round(base, digits) if base is not None else None,
                "delta": delta,
                "direction": direction,
            }
        )
    return rows


def _weekly(series: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for row in series:
        date_value = row.get("date")
        if not date_value:
            continue
        try:
            current = date_type.fromisoformat(str(date_value))
        except ValueError:
            continue
        week_start = (current - timedelta(days=current.weekday())).isoformat()
        buckets.setdefault(week_start, []).append(row)
    rows = []
    for week_start in sorted(buckets)[-8:]:
        items = buckets[week_start]
        rows.append(
            {
                "week_start": week_start,
                "days": len(items),
                "sleep_avg": _mean([row.get("sleep_minutes") for row in items]),
                "efficiency_avg": _mean([row.get("sleep_efficiency") for row in items]),
                "steps_avg": _mean([row.get("steps") for row in items]),
                "active_sum": round(sum(row.get("active_minutes") or 0 for row in items)),
                "rhr_avg": _mean([row.get("resting_hr") for row in items]),
                "hrv_avg": _mean([row.get("hrv_ms") for row in items]),
            }
        )
    return rows


def _facts(recovery: dict, load: dict, rhythm: dict, coverage: dict) -> list[str]:
    """给首页的几条要点。"""
    substantive: list[str] = []
    caveats: list[str] = []

    debt = recovery.get("sleep_debt") or {}
    if debt.get("nights", 0) >= 4 and debt.get("minutes", 0) >= 30:
        hours = debt["minutes"] / 60
        substantive.append(
            t("server.insights._facts.a8ab2ddad", p1=debt["window"], hours=hours, p3=debt["nights"])
        )

    jetlag = rhythm.get("social_jetlag_min")
    if jetlag is not None:
        substantive.append(t("facts.social_jetlag.small" if jetlag < 60 else "facts.social_jetlag", jetlag=jetlag))

    if rhythm.get("sri") is not None:
        substantive.append(
            t("server.insights._facts.a1fe20b99", p1=rhythm["sri"], p2=rhythm["sri_pairs"])
        )

    cos = rhythm.get("cosinor") or {}
    if cos.get("acrophase_hour") is not None:
        substantive.append(
            t("server.insights._facts.719b341ce", p1=round(cos["acrophase_hour"]) % 24, p2=cos["r2"])
        )

    if load.get("steps_ratio") is not None:
        substantive.append(
            t("server.insights._facts.f6d474d69", p1=load["steps_ratio"])
        )

    if load.get("mvpa_minutes_week"):
        weighted = load.get("who_weighted_minutes")
        pct = load.get("who_pct")
        key = "facts.weekly_activity.near" if pct is not None and 100 <= pct <= 200 else (
            "facts.weekly_activity.above" if pct is not None and pct > 200 else "facts.weekly_activity.below"
        )
        substantive.append(t(key, weighted=weighted))

    archived = coverage.get("days_with_archive")
    weakest = coverage.get("weakest_metric_days")
    if archived and weakest is not None and weakest < archived and (
        archived - weakest >= 2 or weakest < archived * 0.9
    ):
        caveats.append(
            t(
                "server.insights._facts.32dda8e94",
                archived=archived,
                p2=_METRIC_LABELS.get(coverage.get("weakest_metric"), t("server.insights._facts.91e454f17")),
                weakest=weakest,
            )
        )

    excluded = coverage.get("excluded_out_of_range") or {}
    if excluded:
        detail = t("separator.list").join(
            t("server.insights._facts.3901996b4", p1=_METRIC_LABELS.get(key, key), value=value)
            for key, value in excluded.items()
        )
        caveats.append(t("server.insights._facts.3930644c9", detail=detail))

    sparse = coverage.get("sparse_days") or {}
    if sparse.get("count"):
        dates = t("separator.list").join(d[-5:] for d in sparse["dates"][:3])
        caveats.append(
            t("server.insights._facts.7eddd966b", p1=sparse["count"], dates=dates)
        )

    if coverage.get("partial_days"):
        caveats.append(t("server.insights._facts.c3d984976", p1=coverage["partial_days"]))

    return substantive + caveats
