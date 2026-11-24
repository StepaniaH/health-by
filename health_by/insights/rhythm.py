"""作息与昼夜节律指标汇总（SRI / 社交时差 / 余弦节律）。"""

from __future__ import annotations

from typing import Any

from health_by import metrics

from .common import MIN_SRI_PAIRS, RHYTHM_WINDOW_DAYS, _days_in_calendar_window


def _rhythm(days: list[dict]) -> dict[str, Any]:
    recent = _days_in_calendar_window(days, metrics.BASELINE_WINDOW_DAYS)
    timing = metrics.sleep_timing(recent)
    skipped = metrics.sri(_days_in_calendar_window(days, RHYTHM_WINDOW_DAYS + 1))
    hourly = metrics.hourly_activity(_days_in_calendar_window(days, RHYTHM_WINDOW_DAYS))
    cos = metrics.cosinor(hourly)
    enough_pairs = skipped["pairs"] >= MIN_SRI_PAIRS
    return {
        "sri": skipped["value"] if enough_pairs else None,
        "sri_pairs": skipped["pairs"],
        "midpoint_sd_min": timing["midpoint_sd_min"],
        "bed_sd_min": timing["bed_sd_min"],
        "wake_sd_min": timing["wake_sd_min"],
        "social_jetlag_min": timing["social_jetlag_min"],
        "midpoint_mean": timing["midpoint_mean"],
        "n_nights": timing["n_nights"],
        "rhythm_days": RHYTHM_WINDOW_DAYS,
        "baseline_days": metrics.BASELINE_WINDOW_DAYS,
        "hourly": hourly,
        "cosinor": cos,
    }
