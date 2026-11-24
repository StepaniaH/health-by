"""交叉关联检验：日历对齐配对、Bartlett AR(1) 有效样本量、Fisher 置信区间与多重比较校正。"""

from __future__ import annotations

import math
from typing import Any

from health_by import metrics
from health_by.i18n import t
from health_by.util import shift_iso

from .common import (
    CORRELATION_ALPHA,
    CORRELATION_PAIRS,
    MIN_CORRELATION_SAMPLES,
    linear_slope,
    pearson,
)


def _strength(
    r: float, ci_low: float | None, ci_high: float | None, *, survives: bool = True
) -> str:
    """相关强度的用词，分两级降级。"""
    if ci_low is None or ci_high is None:
        return metrics.strength_label(r)
    if ci_low <= 0 <= ci_high:
        return t("server.insights._strength.7795b72cc")
    if not survives:
        return t("server.insights._strength.41264413d")
    return metrics.strength_label(r)


def _relation(offset_days: int | None) -> str:
    """两条指标的关系符号：同日用 `×`，次日用 `→`。"""
    return "→" if offset_days else "×"


def _required_samples(r: float | None) -> int | None:
    """要让 |r| 通过 Bonferroni 校正，至少需要多少个配对样本。"""
    if r is None or abs(r) < 1e-6 or abs(r) >= 1:
        return None
    z_critical = metrics.normal_quantile(1 - (CORRELATION_ALPHA / len(CORRELATION_PAIRS)) / 2)
    return int(math.ceil((z_critical / abs(math.atanh(r))) ** 2 + 3))


def _correlation_summary(
    hidden: list[dict], tested: int, shown: int
) -> dict[str, Any]:
    """未通过校正时，界面三行陈述所需的扁平字段。"""
    z_critical = metrics.normal_quantile(1 - (CORRELATION_ALPHA / len(CORRELATION_PAIRS)) / 2)
    candidates: list[tuple[tuple, dict, int]] = []
    for item in hidden:
        r, n = item.get("r"), item.get("n")
        n_eff = item.get("n_eff") or n
        if r is None or not n or n <= 3:
            continue
        required_eff = _required_samples(r)
        if not required_eff:
            continue
        scale = (n / n_eff) if (isinstance(n_eff, (int, float)) and 0 < n_eff < n) else 1.0
        required = int(math.ceil(required_eff * scale))
        entry = {
            "x_label": item.get("x_label"),
            "y_label": item.get("y_label"),
            "r": r,
            "n": n,
            "offset_days": item.get("offset_days"),
            "relation": _relation(item.get("offset_days")),
        }
        key = (
            max(0, required - n),
            required,
            str(item.get("x_label")),
            str(item.get("y_label")),
        )
        candidates.append((key, entry, required))
    closest = required_n = eta_days = None
    if candidates:
        candidates.sort(key=lambda item: item[0])
        _, closest, required_n = candidates[0]
        eta_days = max(0, required_n - closest["n"])
    return {
        "correlations_shown": shown,
        "correlations_closest": closest,
        "correlations_required_n": required_n,
        "correlations_eta_days": eta_days,
        "correlations_alpha_adjusted": round(CORRELATION_ALPHA / len(CORRELATION_PAIRS), 4),
        "correlations_z_critical": round(z_critical, 3),
    }


def _correlations(
    series: list[dict], *, include_hidden: bool = False
) -> tuple[list[dict], int, int, list[dict]]:
    """返回 (本次下发的结论, 实际检验组数, 通过校正条数, 其余/全部原始条目)。"""
    by_date = {str(row["date"]): row for row in series if row.get("date")}
    found: list[dict] = []
    for x_key, y_key, x_label, y_label, offset in CORRELATION_PAIRS:
        pair_dates: list[str] = []
        xs: list[float] = []
        ys: list[float] = []
        for row in series:
            row_date = row.get("date")
            if not row_date:
                continue
            if offset == 0:
                target_row = row
            else:
                try:
                    target_date = shift_iso(str(row_date), offset)
                except ValueError:
                    continue
                target_row = by_date.get(target_date)
            if target_row is None:
                continue
            x_value = row.get(x_key)
            y_value = target_row.get(y_key)
            if x_value is None or y_value is None:
                continue
            pair_dates.append(str(row_date))
            xs.append(float(x_value))
            ys.append(float(y_value))
        if len(xs) < MIN_CORRELATION_SAMPLES:
            continue
        r = pearson(xs, ys)
        if r is None:
            continue
        slope = linear_slope(xs, ys)
        n_eff = metrics.effective_sample_size(pair_dates, xs, ys)
        ci_low, ci_high = metrics.fisher_ci(r, n_eff)
        found.append(
            {
                "x": x_key,
                "y": y_key,
                "x_label": x_label,
                "y_label": y_label,
                "offset_days": offset,
                "relation": _relation(offset),
                "_raw_r": r,
                "_raw_n_eff": n_eff,
                "r": round(r, 3),
                "n": len(xs),
                "n_eff": round(n_eff, 1),
                "ci_low": ci_low,
                "ci_high": ci_high,
                "strength": None,
                "slope": round(slope, 5) if slope is not None else None,
            }
        )
    tests = max(1, len(CORRELATION_PAIRS))
    for item in found:
        raw_r = item.pop("_raw_r")
        raw_n_eff = item.pop("_raw_n_eff")
        ci_low, ci_high = item["ci_low"], item["ci_high"]
        item["excludes_zero"] = (
            ci_low is not None and ci_high is not None and (ci_low > 0 or ci_high < 0)
        )
        item["alpha_adjusted"] = round(CORRELATION_ALPHA / tests, 4)
        # 用未舍入的 r 与 Bartlett 有效样本量做 Fisher z 检验，避免临界值因展示舍入误通过。
        item["survives_correction"] = bool(
            item["excludes_zero"]
            and metrics.correlation_is_significant(raw_r, raw_n_eff, CORRELATION_ALPHA / tests)
        )
        item["strength"] = _strength(
            raw_r, ci_low, ci_high, survives=item["survives_correction"]
        )
    found.sort(
        key=lambda item: (
            min(abs(item["ci_low"]), abs(item["ci_high"])) if item["excludes_zero"] else 0.0,
            abs(item["r"]),
        ),
        reverse=True,
    )
    tested = len(found)
    kept = [item for item in found if item["survives_correction"]]
    if include_hidden:
        return found, tested, len(kept), found
    return kept, tested, len(kept), [item for item in found if not item["survives_correction"]]
