"""首现证据与离线效应量工具；统计水平变化检测未接入产品判据。"""

from health_by.i18n import t

import math
import sys
from datetime import date, timedelta
from statistics import mean, pvariance

BREAK_EFFECT_SIZE = 0.8


def _dated(values):
    result = {}
    for day, value in values:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError(t('server.regime._dated.422032aca'))
        if day in result:
            raise ValueError(t('server.regime._dated.8e35f72c7'))
        if value is not None and not isinstance(value, bool) and math.isfinite(value):
            result[day] = float(value)
    return sorted(result.items())


def detect_break(values: list[tuple[str, float]], min_segment: int = 14) -> dict | None:
    """研究用候选：遍历切点取最大效应量；误报未控制，不用于产品基线或提示。"""
    if isinstance(min_segment, bool) or not isinstance(min_segment, int) or min_segment < 1:
        raise ValueError(t('server.regime.detect_break.27a404f6c'))
    ordered = _dated(values)
    if len(ordered) < max(3, min_segment * 2):
        return None
    numbers = [value for _, value in ordered]
    best = None
    for index in range(min_segment, len(numbers) - min_segment + 1):
        before, after = numbers[:index], numbers[index:]
        before_mean, after_mean = mean(before), mean(after)
        delta = after_mean - before_mean
        # 两组组内平方和 / (总样本数 - 2)，即标准 pooled sample variance。
        pooled_sd = math.sqrt((len(before) * pvariance(before) + len(after) * pvariance(after)) / (len(numbers) - 2))
        # 完全恒定的两段有零分母；饱和到最大有限数，保证接口 JSON 可编码。
        d = min(abs(delta) / pooled_sd, sys.float_info.max) if pooled_sd else (sys.float_info.max if delta else 0.0)
        if d >= BREAK_EFFECT_SIZE and (best is None or d > best["d"]):
            best = {"date": ordered[index][0], "before_mean": before_mean,
                    "after_mean": after_mean, "delta": delta, "d": d}
    return best


def field_appears(
    pairs: list[tuple[str, float | None]], *, missing_days: int = 7,
    persistent_days: int = 3, zero_is_missing: bool = False,
) -> str | None:
    """仅识别有存档证据的持续首现；不能把未存档日期或合法零值当作缺测。"""
    for length in (missing_days, persistent_days):
        if isinstance(length, bool) or not isinstance(length, int) or length < 1:
            raise ValueError(t('server.regime.field_appears.fd10f927c'))
    observed = {}
    for day, value in pairs:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError(t('server.regime.field_appears.422032aca'))
        if day in observed:
            raise ValueError(t('server.regime.field_appears.8e35f72c7'))
        valid = value is not None and not isinstance(value, bool) and math.isfinite(value)
        observed[day] = float(value) if valid and (value != 0 or not zero_is_missing) else None
    # 已有有效读数之后的断档/恢复不叫“字段首现”。
    first = next((day for day in sorted(observed) if observed[day] is not None), None)
    if first is None:
        return None
    onset = date.fromisoformat(first)
    preceding = [(onset - timedelta(days=back)).isoformat() for back in range(1, missing_days + 1)]
    following = [(onset + timedelta(days=ahead)).isoformat() for ahead in range(persistent_days)]
    if not all(day in observed and observed[day] is None for day in preceding):
        return None
    return first if all(day in observed and observed[day] is not None for day in following) else None
