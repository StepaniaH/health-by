"""日观测的周内/周间摘要与显示方式；不把变化归因为测量噪声。"""

from health_by.i18n import t

import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, pvariance

from .regime import _dated

WINDOW_DAYS = 56
MIN_WEEKS = 3
ROLLING_SIZE = 7
ROLLING_THRESHOLD = 0.4
# sedentary 是产品规格名，sedentary_minutes 是归一化字段名；两者均固定显示日值。
BEHAVIOR_METRICS = frozenset({"steps", "active_minutes", "zone_minutes", "distance_km", "floors", "calories_out", "sedentary", "sedentary_minutes"})


def rolling(values: list[float | None], size: int = ROLLING_SIZE) -> list[float | None]:
    """右对齐；缺测占日历位置但不补零，窗口全缺测才返回 None。"""
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError(t('server.reliability.rolling.cdc86b8c8'))
    result = []
    for index in range(len(values)):
        window = [value for value in values[max(0, index - size + 1):index + 1]
                  if value is not None and not isinstance(value, bool) and math.isfinite(value)]
        result.append(mean(window) if window else None)
    return result


def display_mode(metric: str, reliability: float | None) -> str:
    if metric in BEHAVIOR_METRICS or reliability is None:
        return "daily"
    return "rolling7" if reliability < ROLLING_THRESHOLD else "daily"


def _weeks(values: list[tuple[str, float]], window_days: int) -> list[list[float]]:
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
        raise ValueError(t('server.reliability._weeks.5cb680bc9'))
    ordered = _dated(values)
    if not ordered:
        return []
    start = date.fromisoformat(ordered[-1][0]) - timedelta(days=window_days - 1)
    weeks = defaultdict(list)
    for day, value in ordered:
        current = date.fromisoformat(day)
        if current >= start:
            iso = current.isocalendar()
            weeks[(iso.year, iso.week)].append(value)
    return [group for group in weeks.values() if len(group) >= 3]


def icc(values: list[tuple[str, float]], window_days: int = WINDOW_DAYS) -> float | None:
    """按本次规格计算总体方差比，不是 ANOVA ICC(1)。各合格周等权。"""
    weeks = _weeks(values, window_days)
    if len(weeks) < MIN_WEEKS:
        return None
    between = pvariance([mean(week) for week in weeks])
    within = mean(pvariance(week) for week in weeks)
    return between / (between + within) if between + within > 0 else None


STATE_PRECISION = {"sleep_minutes": 0, "resting_hr": 1, "hrv_ms": 1,
                   "breathing_rate": 1, "spo2": 1, "skin_temp_delta": 2}


def calendar_averages(observations: list[tuple[str, dict]], selected: str, keys=None) -> dict:
    """所选日结束的日历 7 日均值；与卡片共用，不受显示模式影响，不计算行为量。"""
    end = date.fromisoformat(selected)
    points = dict(observations)
    calendar = [(end - timedelta(days=back)).isoformat() for back in range(ROLLING_SIZE - 1, -1, -1)]
    result = {}
    for key in (keys if keys is not None else STATE_PRECISION):
        if key not in STATE_PRECISION:
            continue
        value = rolling([points.get(day, {}).get(key) for day in calendar])[-1]
        result[key] = round(value, STATE_PRECISION[key]) if value is not None else None
    return result


def assess(observations: list[tuple[str, dict]], selected: str, current: dict) -> dict:
    """七个比较指标同源；显示值在后端计算，日快照与存档保持原值。"""
    scores, modes, display, insufficient = {}, {}, {}, {}
    week_counts, mean_days = {}, {}
    end = date.fromisoformat(selected)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    points = {day: values for day, values in observations if start.isoformat() <= day <= selected}
    averages = calendar_averages(observations, selected, current)
    for key, daily in current.items():
        pairs = [(day, values[key]) for day, values in points.items() if values.get(key) is not None]
        weeks = _weeks(pairs, WINDOW_DAYS)
        week_counts[key] = len(weeks)
        mean_days[key] = mean(len(week) for week in weeks) if weeks else None
        score = icc(pairs)
        mode = display_mode(key, score)
        scores[key], modes[key], insufficient[key] = score, mode, score is None
        value = daily
        if mode == "rolling7":
            value = averages[key]
        display[key] = value
    return {"reliability": scores, "display_mode": modes, "display_values": display, "insufficient": insufficient,
            "reliability_basis": {"window_days": WINDOW_DAYS, "min_weeks": MIN_WEEKS, "threshold": ROLLING_THRESHOLD,
                                  "weeks": week_counts, "mean_days_per_week": mean_days,
                                  "method": "variance_ratio",
                                  "definition": t('server.reliability.assess.46bb59021')}}
