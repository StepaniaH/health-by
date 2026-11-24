"""分析层公共常量与基础统计/序列原语。"""

from __future__ import annotations

import math
from datetime import date as date_type
from datetime import timedelta
from statistics import mean, median
from typing import Any

from health_by import metrics
from health_by.i18n import t

INSIGHTS_DAYS_DEFAULT = 30
# 窗口常量都应集中在本层，api 反向引用，避免"同一个窗口两处各写一份"。
SUMMARY_TREND_DAYS = 14
# 目标达成统计的窗口（"近 N 天里达标几天"）。前端曾自己 slice(-28)，窗口只有浏览器知道。
ACHIEVEMENTS_WINDOW_DAYS = 28
INSIGHTS_DAYS_MAX = 90
MIN_CORRELATION_SAMPLES = 10
BAND_WINDOW_DAYS = 7
BAND_MIN_SAMPLES = 3
SHORT_WINDOW_DAYS = 7
RHYTHM_WINDOW_DAYS = 14
# SRI 至少要这么多对相邻日才有意义；1～2 对的数值是噪声（实测过 1 对给出 -15）
MIN_SRI_PAIRS = 7

# 多重比较：CORRELATION_PAIRS 共 12 对，同时检验 12 次。
# 不做校正时，在纯噪声上也能“发现”1–2 个看起来显著的相关（α=0.05 时预期 0.6 个）。
CORRELATION_ALPHA = 0.05

# WHO 活动量折算：1 分钟高强度 ≈ 2 分钟中等强度（WHO 2020 指南的等价换算）
WHO_VIGOROUS_MULTIPLIER = 2.0

# 目标达成 / 评估阈值（固定、可核对）
SLEEP_GOOD_RATIO = 0.95
SLEEP_LOW_RATIO = 0.85
EFFICIENCY_LOW_DROP = 5.0
WHO_WEEKLY_ACTIVE_MINUTES = 150

# 分析层会剔除越界读数，但必须回报剔除了几条——否则“基于 30 天”其实可能是 22 天。
BOUNDS: dict[str, tuple[float, float]] = metrics.BOUNDS

WEEKDAY_WORKDAYS = 5  # 周一~周五；weekday() 0=周一

# 按指标明细覆盖 bands 里的全部指标
BAND_METRICS = ("resting_hr", "hrv_ms", "sleep_minutes", "steps", "active_minutes")
# 顶层 `band_outside` 的全量口径**保持原来的 4 项**：它已有消费方，
# 悄悄把 active_minutes 并进去会让界面上的百分比无端变一次（50% → 47%），
# 而数据没变。要扩就单独说，别顺手改既有字段的含义。
LEGACY_BAND_OUTSIDE_METRICS = ("resting_hr", "hrv_ms", "sleep_minutes", "steps")
BAND_OUTSIDE_MIN_DAYS = 7  # 与前端 `countValid(points) < 7 则跳过` 一致

# (x 指标, y 指标, x 名称, y 名称, y 相对 x 的天偏移)
CORRELATION_PAIRS: tuple[tuple[str, str, str, str, int], ...] = (
    ("sleep_minutes", "resting_hr", t("server.insights.labels.98439c55d"), t("server.insights.labels.e9a48bd3e"), 0),
    ("sleep_minutes", "hrv_ms", t("server.insights.labels.98439c55d"), "HRV", 0),
    ("deep_minutes", "resting_hr", t("server.insights.labels.ee3d90ef1"), t("server.insights.labels.e9a48bd3e"), 0),
    ("sleep_efficiency", "hrv_ms", t("server.insights.labels.dc4a0545d"), "HRV", 0),
    ("sleep_mid", "resting_hr", t("server.insights.labels.a42c0af9e"), t("server.insights.labels.e9a48bd3e"), 0),
    ("sleep_mid", "hrv_ms", t("server.insights.labels.a42c0af9e"), "HRV", 0),
    ("sleep_minutes", "steps", t("server.insights.labels.98439c55d"), t("server.insights.labels.b0d4e2efd"), 0),
    ("steps", "sleep_minutes", t("server.insights.labels.2986c7ee9"), t("server.insights.labels.431da32da"), 1),
    ("steps", "deep_minutes", t("server.insights.labels.2986c7ee9"), t("server.insights.labels.e23db13c5"), 1),
    ("steps", "resting_hr", t("server.insights.labels.2986c7ee9"), t("server.insights.labels.a3f0838e8"), 1),
    ("active_minutes", "sleep_minutes", t("server.insights.labels.301220d3d"), t("server.insights.labels.431da32da"), 1),
    ("sedentary_minutes", "resting_hr", t("server.insights.labels.52eaa93d2"), t("server.insights.labels.e9a48bd3e"), 0),
)

_METRIC_LABELS = {
    "sleep_minutes": t("server.insights.labels.98439c55d"),
    "sleep_efficiency": t("server.insights.labels.dc4a0545d"),
    "resting_hr": t("server.insights.labels.e9a48bd3e"),
    "hrv_ms": "HRV",
    "steps": t("server.insights.labels.2986c7ee9"),
    "active_minutes": t("server.insights.labels.301220d3d"),
    "sedentary_minutes": t("server.insights.labels.52eaa93d2"),
}


def _to_float(value: Any) -> float | None:
    return metrics.to_float(value)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = mean(xs)
    my = mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def linear_slope(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = mean(xs)
    my = mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def _time_decimal(value: Any) -> float | None:
    """ISO 时间串 → 连续小数小时（凌晨时刻 +24，便于跨夜比较：23:06→23.1，00:30→24.5）。"""
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        hh = int(value[11:13])
        mm = int(value[14:16])
    except ValueError:
        return None
    t_val = hh + mm / 60
    if t_val < 15:
        t_val += 24
    return round(t_val, 2)


def _sleep_bed_wake(start_raw: Any, end_raw: Any) -> tuple[float | None, float | None]:
    """根据同一段睡眠的起止时间展开连续小时，避免午后起床被各自取模折返 12 小时。"""
    start = metrics._parse_iso(start_raw)
    end = metrics._parse_iso(end_raw)
    if start is not None and end is not None and end > start:
        duration_hours = (end - start).total_seconds() / 3600
        if duration_hours <= 24:
            bed = start.hour + start.minute / 60 + start.second / 3600
            if bed < 18:
                bed += 24
            wake = bed + duration_hours
            return round(bed, 2), round(wake, 2)
    return _time_decimal(start_raw), _time_decimal(end_raw)


def _ratio(value: float | None, goal: Any) -> float | None:
    target = _to_float(goal)
    if value is None or not target:
        return None
    raw = value / target
    rounded = round(raw, 3)
    if raw < 1.0 and rounded >= 1.0:
        return 0.999
    return rounded


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(mean(clean), 1) if clean else None


def _median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(median(clean), 1) if clean else None


def _bounded(value: float | None, low: float, high: float) -> float | None:
    """剔除生理上不可能或明显损坏的读数（保留原始载荷，只影响分析层）。"""
    if value is None or not (low <= value <= high):
        return None
    return value


def _exclusions(days: list[dict]) -> dict[str, int]:
    """统计每个指标因越界被分析层剔除的天数（与 `_series` 的取值路径一致）。"""
    counts = {key: 0 for key in BOUNDS}
    for day in days:
        activity = day.get("activity") or {}
        heart = day.get("heart") or {}
        sleep = day.get("sleep") or {}
        stages = sleep.get("stages") or {}
        body = day.get("body") or {}
        raw = {
            "sleep_minutes": _to_float(sleep.get("total_minutes")),
            "sleep_efficiency": _to_float(sleep.get("efficiency")),
            "resting_hr": _to_float(heart.get("resting_hr")),
            "hrv_ms": _to_float(heart.get("hrv_ms")),
            "spo2": _to_float(heart.get("spo2")),
            "breathing_rate": _to_float(heart.get("breathing_rate")),
            "weight_kg": _to_float(body.get("weight_kg")),
            "body_fat": _to_float(body.get("body_fat_pct")),
        }
        for key, value in raw.items():
            if value is None:
                continue
            low, high = BOUNDS[key]
            if not (low <= value <= high):
                counts[key] += 1
        _ = activity, stages
    return counts


def _days_in_calendar_window(days: list[dict], size: int) -> list[dict]:
    dated: list[tuple[date_type, dict]] = []
    for day in days:
        raw_date = day.get("date")
        if not raw_date:
            continue
        try:
            dated.append((date_type.fromisoformat(str(raw_date)), day))
        except ValueError:
            continue
    if not dated:
        return days[-size:]
    end = max(d for d, _ in dated)
    start = end - timedelta(days=max(1, size) - 1)
    return [day for d, day in sorted(dated, key=lambda item: item[0]) if start <= d <= end]


# 窗口与基线定义已收敛到 metrics.py（全站唯一），这里只保留内部别名，
# 免得改一大堆调用点；两处不会再各自漂移。
_dense_window = metrics.dense_window


def _window_values(series: list[dict], key: str, size: int) -> list[float | None]:
    """按**日历**取最后 `size` 天（一天一个元素，缺测填 None）。"""
    return _dense_window(series, key, size, offset_days=0)


def _baseline_before(series: list[dict], key: str, window: int = metrics.BASELINE_WINDOW_DAYS):
    """当前日之前的个人基线：(中位数, 标准差)。定义见 metrics.baseline_before。"""
    return metrics.baseline_before(series, key, window=window)
