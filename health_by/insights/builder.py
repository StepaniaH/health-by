"""insights 主入口：编排序列、恢复、负荷、节律、相关性与周报输出。"""

from __future__ import annotations

from typing import Any

from health_by import metrics

from .common import (
    ACHIEVEMENTS_WINDOW_DAYS,
    BAND_WINDOW_DAYS,
    INSIGHTS_DAYS_DEFAULT,
    RHYTHM_WINDOW_DAYS,
    SHORT_WINDOW_DAYS,
    SUMMARY_TREND_DAYS,
)
from .correlations import _correlation_summary, _correlations
from .recovery import _coverage, _facts, _load, _recovery, _summary_table, _weekly
from .rhythm import _rhythm
from .series import (
    _achievements,
    _achievements_summary,
    _band,
    _band_outside,
    _band_outside_by_metric,
    _heatmap,
    _series,
    _timing,
    _weekday,
    _weekday_summary,
)


def build_insights(
    days: list[dict],
    goals: dict,
    *,
    end_date: str | None = None,
    window_days: int = INSIGHTS_DAYS_DEFAULT,
    include_hidden: bool = False,
    history_days: list[dict] | None = None,
) -> dict[str, Any]:
    """汇总分析结果。

    `include_hidden` 仅供调试（`/api/v1/insights?include_hidden=1`）：
    默认 `correlations` 仅下发通过多重比较校正的相关结论（AI 出口同样只消费该列表）；
    同时返回 `correlations_tested` / `correlations_hidden` 计数与 `correlations_candidates`
    候选摘要（含 `x`/`y`/`r`/`n`/`survives_correction`，未通过项的 `slope` 置为 `None`），
    供界面展示"已检验但未通过校正"的透明度说明。
    `history_days` 提供额外的窗口前历史，供个人基线、ACWR 负荷、节律与 7 日滚动带复用。
    """
    valid_days = [day for day in (days or []) if isinstance(day, dict) and day.get("date")]
    series = _series(valid_days, goals or {})
    if history_days is not None:
        by_date = {
            str(day["date"]): day
            for day in history_days
            if isinstance(day, dict) and day.get("date")
        }
        for day in valid_days:
            by_date[str(day["date"])] = day
        full_days = [by_date[key] for key in sorted(by_date)]
        if end_date:
            full_days = [day for day in full_days if str(day["date"]) <= end_date]
        full_series = _series(full_days, goals or {})
    else:
        full_days = valid_days
        full_series = series
    window_date_set = {str(row.get("date")) for row in series if row.get("date")}
    dates = [row["date"] for row in series]
    recovery = _recovery(full_series, full_days, goals or {})
    load = _load(full_series, full_days)
    rhythm = _rhythm(full_days)
    coverage = _coverage(series, valid_days, window_days)
    weekday_rows = _weekday(series)
    correlations, correlations_tested, correlations_kept, correlations_rejected = _correlations(
        series, include_hidden=include_hidden
    )
    band_series = full_series if history_days is not None else series
    bands = {}
    for metric_key in ("resting_hr", "hrv_ms", "sleep_minutes", "steps", "active_minutes"):
        computed_band = _band(band_series, metric_key)
        if history_days is not None:
            computed_band = [entry for entry in computed_band if entry.get("date") in window_date_set]
        bands[metric_key] = computed_band
    return {
        "from": dates[0] if dates else None,
        "to": end_date or (dates[-1] if dates else None),
        "window_days": window_days,
        "n_days": len(series),
        "series": series,
        "correlations": correlations,
        "correlations_tested": correlations_tested,
        "correlations_hidden": correlations_tested - correlations_kept,
        "correlations_candidates": sorted(
            {
                (item["x"], item["y"], item["offset_days"]): {
                    **{
                        key: item[key]
                        for key in (
                            "x",
                            "y",
                            "x_label",
                            "y_label",
                            "offset_days",
                            "r",
                            "n",
                            "survives_correction",
                        )
                    },
                    "slope": item["slope"] if item["survives_correction"] else None,
                }
                for item in (*correlations, *correlations_rejected)
            }.values(),
            key=lambda pair: abs(pair["r"]) if pair["r"] is not None else -1.0,
            reverse=True,
        ),
        **_correlation_summary(correlations_rejected, correlations_tested, correlations_kept),
        "weekday": weekday_rows,
        "weekday_summary": _weekday_summary(weekday_rows),
        "band_outside": _band_outside(band_series, bands),
        "bands_outside": _band_outside_by_metric(band_series, bands),
        "heatmap": _heatmap(valid_days),
        "bands": bands,
        "timing": _timing(series),
        "achievements": _achievements(series),
        "achievements_summary": _achievements_summary(series, goals=goals or {}, end_date=end_date),
        "windows": {
            "band": BAND_WINDOW_DAYS,
            "short": SHORT_WINDOW_DAYS,
            "baseline": metrics.BASELINE_WINDOW_DAYS,
            "summary_trend": SUMMARY_TREND_DAYS,
            "achievements": ACHIEVEMENTS_WINDOW_DAYS,
            "rhythm": RHYTHM_WINDOW_DAYS,
        },
        "recovery": recovery,
        "load": load,
        "rhythm": rhythm,
        "coverage": coverage,
        "summary_table": _summary_table(full_series),
        "weekly": _weekly(series),
        "facts": _facts(recovery, load, rhythm, coverage),
    }
