"""分析层：把每日存档整理成恢复、负荷、节律、交叉关联与周报表。

设计原则：
- 只使用本地已存档的数据，零额外 API 调用；
- 每一项都能追溯到原始测量、个人基线或公开方法（SRI / 社交时差 / 余弦节律 / ACWR）；
- 不做诊断，不给医疗建议；评级规则固定且可见，便于核对。
"""

from __future__ import annotations

from health_by.i18n import t

import math
from datetime import date as date_type
from datetime import timedelta
from statistics import mean, median, pstdev
from typing import Any

from . import metrics

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

# (x 指标, y 指标, x 名称, y 名称, y 相对 x 的天偏移)
CORRELATION_PAIRS: tuple[tuple[str, str, str, str, int], ...] = (
    ("sleep_minutes", "resting_hr", t('server.insights.labels.98439c55d'), t('server.insights.labels.e9a48bd3e'), 0),
    ("sleep_minutes", "hrv_ms", t('server.insights.labels.98439c55d'), "HRV", 0),
    ("deep_minutes", "resting_hr", t('server.insights.labels.ee3d90ef1'), t('server.insights.labels.e9a48bd3e'), 0),
    ("sleep_efficiency", "hrv_ms", t('server.insights.labels.dc4a0545d'), "HRV", 0),
    ("sleep_mid", "resting_hr", t('server.insights.labels.a42c0af9e'), t('server.insights.labels.e9a48bd3e'), 0),
    ("sleep_mid", "hrv_ms", t('server.insights.labels.a42c0af9e'), "HRV", 0),
    ("sleep_minutes", "steps", t('server.insights.labels.98439c55d'), t('server.insights.labels.b0d4e2efd'), 0),
    ("steps", "sleep_minutes", t('server.insights.labels.2986c7ee9'), t('server.insights.labels.431da32da'), 1),
    ("steps", "deep_minutes", t('server.insights.labels.2986c7ee9'), t('server.insights.labels.e23db13c5'), 1),
    ("steps", "resting_hr", t('server.insights.labels.2986c7ee9'), t('server.insights.labels.a3f0838e8'), 1),
    ("active_minutes", "sleep_minutes", t('server.insights.labels.301220d3d'), t('server.insights.labels.431da32da'), 1),
    ("sedentary_minutes", "resting_hr", t('server.insights.labels.52eaa93d2'), t('server.insights.labels.e9a48bd3e'), 0),
)


# ---------- 基础统计 ----------


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
    t = hh + mm / 60
    if t < 15:
        t += 24
    return round(t, 2)


def _ratio(value: float | None, goal: Any) -> float | None:
    target = _to_float(goal)
    if value is None or not target:
        return None
    return round(value / target, 3)


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(mean(clean), 1) if clean else None


def _median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(median(clean), 1) if clean else None


# ---------- 序列 ----------


def _bounded(value: float | None, low: float, high: float) -> float | None:
    """剔除生理上不可能或明显损坏的读数（保留原始载荷，只影响分析层）。"""
    if value is None or not (low <= value <= high):
        return None
    return value


# 分析层会剔除越界读数，但必须回报剔除了几条——否则“基于 30 天”其实可能是 22 天。
BOUNDS: dict[str, tuple[float, float]] = {
    "sleep_minutes": (0, 960),
    "sleep_efficiency": (0, 100),
    "resting_hr": (25, 220),
    "hrv_ms": (5, 250),
    "spo2": (50, 100),
    "breathing_rate": (4, 40),
    "weight_kg": (20, 300),
    "body_fat": (1, 70),
}


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


def _series(days: list[dict], goals: dict) -> list[dict]:
    rows: list[dict] = []
    for day in days:
        activity = day.get("activity") or {}
        heart = day.get("heart") or {}
        sleep = day.get("sleep") or {}
        stages = sleep.get("stages") or {}
        body = day.get("body") or {}
        sleep_minutes = _bounded(_to_float(sleep.get("total_minutes")), 0, 960)
        bed = _time_decimal(sleep.get("start_time"))
        wake = _time_decimal(sleep.get("end_time"))
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
                "sleep_efficiency": _bounded(_to_float(sleep.get("efficiency")), 0, 100),
                "resting_hr": _bounded(_to_float(heart.get("resting_hr")), 25, 220),
                "hrv_ms": _bounded(_to_float(heart.get("hrv_ms")), 5, 250),
                "spo2": _bounded(_to_float(heart.get("spo2")), 50, 100),
                "breathing_rate": _bounded(_to_float(heart.get("breathing_rate")), 4, 40),
                "skin_temp_delta": _to_float(heart.get("skin_temp_delta_c")),
                "weight_kg": _bounded(_to_float(body.get("weight_kg")), 20, 300),
                "body_fat": _bounded(_to_float(body.get("body_fat_pct")), 1, 70),
                "bed": bed,
                "wake": wake,
                "sleep_mid": round((bed + wake) / 2, 2) if bed is not None and wake is not None else None,
                "steps_ratio": _ratio(_to_float(activity.get("steps")), goals.get("steps")),
                "sleep_ratio": _ratio(sleep_minutes, goals.get("sleep_minutes")),
            }
        )
    return rows


def _window_values(series: list[dict], key: str, size: int) -> list[float | None]:
    """按**日历**取最后 `size` 天（一天一个元素，缺测填 None）。

    不能直接用 `series[-size:]`：日数据是按“有数据的日期”存的（`storage.get_day_window`
    只返回存在的日期），序列里相邻两行可能相差好几天。按行切片会把“最近 7 天”
    拉长成最近十几个日历天，负荷比、基线、滚动带全部跟着偏。
    """
    return _dense_window(series, key, size, offset_days=0)


# 窗口与基线定义已收敛到 metrics.py（全站唯一），这里只保留内部别名，
# 免得改一大堆调用点；两处不会再各自漂移。
_dense_window = metrics.dense_window


def _baseline_before(series: list[dict], key: str, window: int = metrics.BASELINE_WINDOW_DAYS):
    """当前日之前的个人基线：(中位数, 标准差)。定义见 metrics.baseline_before。"""
    return metrics.baseline_before(series, key, window=window)


# ---------- 原本只活在前端的两个口径（上移到这里，判断留在一处）----------

WEEKDAY_WORKDAYS = 5  # 周一~周五；weekday() 0=周一


def _weekday_summary(weekday_rows: list[dict]) -> dict[str, Any]:
    """工作日 vs 周末的步数日均与差值百分比。

    以前这两句只由前端从 `weekday[].steps_avg` 再算一遍，后端无法复核、也测不到。
    口径与前端原来那句文案一致：两侧各取日均，差值百分比相对**工作日**。
    """
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


# 按指标明细覆盖 bands 里的全部指标
BAND_METRICS = ("resting_hr", "hrv_ms", "sleep_minutes", "steps", "active_minutes")
# 顶层 `band_outside` 的全量口径**保持原来的 4 项**：它已有消费方，
# 悄悄把 active_minutes 并进去会让界面上的百分比无端变一次（50% → 47%），
# 而数据没变。要扩就单独说，别顺手改既有字段的含义。
LEGACY_BAND_OUTSIDE_METRICS = ("resting_hr", "hrv_ms", "sleep_minutes", "steps")
BAND_OUTSIDE_MIN_DAYS = 7  # 与前端 `countValid(points) < 7 则跳过` 一致


def _band_outside_by_metric(series: list[dict]) -> dict[str, dict[str, Any]]:
    """按指标统计"当日值落在自身 7 日均值 ±1σ 之外"的点数与比例。

    只统计窗口内有效天数 ≥ `BAND_OUTSIDE_MIN_DAYS` 的指标（与前端原来的跳过规则一致）。
    ±1σ 按设计本来就覆盖不了约三分之一的日子，所以带外只表示"与近期均值不同"。
    """
    out: dict[str, dict[str, Any]] = {}
    for metric in BAND_METRICS:
        band = _band(series, metric)
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


def _band_outside(series: list[dict]) -> dict[str, Any]:
    """四个（现有五个）基线带合并的全量口径。

    顶层 `band_outside` 保留全量语义（前端已有消费方），按指标的明细放在
    `bands_outside` —— **不能**塞进 `bands.<metric>`：那里是数组，
    `bands.hrv_ms.outside` 在 JSON 里根本不存在这种形状，硬改会同时打断
    所有读 `bands[key]` 的调用方。
    """
    per_metric = _band_outside_by_metric(series)
    legacy = {key: value for key, value in per_metric.items() if key in LEGACY_BAND_OUTSIDE_METRICS}
    points = sum(entry["points"] for entry in legacy.values())
    outside = sum(entry["outside"] for entry in legacy.values())
    return {
        "points": points,
        "outside": outside,
        "percent": round(outside / points * 100) if points else 0,
    }


# ---------- 关联 ----------


def _strength(
    r: float, ci_low: float | None, ci_high: float | None, *, survives: bool = True
) -> str:
    """相关强度的用词，分两级降级。

    同时出现「强」和「未通过校正」对非统计背景的读者是自相矛盾的：
    r=0.6 看起来很强，但它完全可能是 12 次检验里撞出来的那一次。
    所以只要没通过校正，就不给强度词。
    """
    if ci_low is None or ci_high is None:
        return metrics.strength_label(r)
    if ci_low <= 0 <= ci_high:
        return t('server.insights._strength.7795b72cc')
    if not survives:
        return t('server.insights._strength.41264413d')
    return metrics.strength_label(r)


def _relation(offset_days: int | None) -> str:
    """两条指标的关系符号：同日用 `×`，次日用 `→`。

    由后端下发，前端不要自己由 `offset_days` 翻译——今晚就出过一次
    "文案写 `→`、界面画 `×`"，原因正是前端把 `x_label → y_label` 中间那个箭头
    当成了通用分隔符。**符号也是结论，判断留在一处。**

    `offset_days` 恒为 0 或 1（全部来自 `CORRELATION_PAIRS` 常量表），`None` 不会出现。
    因此下面 `return "→" if offset_days else "×"` 的 else 分支只覆盖 0，不覆盖"未知"。
    若将来真的引入第三种偏移，这里要改成对 `None` 返回中性值（前端已具备该兜底：
    `relation` 为空时渲染「甲与乙」），而不是继续把未知当成同日。
    """
    return "→" if offset_days else "×"


def _required_samples(r: float | None) -> int | None:
    """要让 |r| 通过 Bonferroni 校正，至少需要多少个配对样本。

    推导（下一个人要能复核，别只抄结论数字）：
        Bonferroni 调整后 α_adj = CORRELATION_ALPHA / len(CORRELATION_PAIRS)
        双尾临界 z_c = normal_quantile(1 - α_adj/2)          # α_adj=0.0042 时 z_c=2.865
        Fisher 检验统计量 z = |atanh(r)| · √(n-3)
        通过条件 z > z_c  ⇒  n > (z_c / |atanh(r)|)² + 3
    """
    if r is None or abs(r) < 1e-6 or abs(r) >= 1:
        return None
    z_critical = metrics.normal_quantile(1 - (CORRELATION_ALPHA / len(CORRELATION_PAIRS)) / 2)
    return int(math.ceil((z_critical / abs(math.atanh(r))) ** 2 + 3))


def _correlation_summary(
    hidden: list[dict], tested: int, shown: int
) -> dict[str, Any]:
    """未通过校正时，界面三行陈述所需的扁平字段（字段名由前端 @forge 约定）。

    键名固定为前端读取的 `correlations_*` 一族，避免嵌套带来的又一次改名。
    "最接近的一组"按**检验统计量 |z| 最大**挑，不是按 |r| 最大：
    n 不同的时候，|r| 更大的那一组不一定更接近通过。

    `correlations_eta_days` 按 **required_n − 当前 n** 算，不按日历推算用户的记录节奏
    —— 他可能一天记两次或漏记，按日历算会和实际体验对不上。
    """
    z_critical = metrics.normal_quantile(1 - (CORRELATION_ALPHA / len(CORRELATION_PAIRS)) / 2)
    candidates: list[tuple[tuple, dict, int]] = []
    for item in hidden:
        r, n = item.get("r"), item.get("n")
        if r is None or not n or n <= 3:
            continue
        required = _required_samples(r)
        if not required:
            continue
        z_value = abs(math.atanh(max(-0.999999, min(0.999999, abs(r))))) * math.sqrt(n - 3)
        entry = {
            "x_label": item.get("x_label"),
            "y_label": item.get("y_label"),
            "r": r,
            "n": n,
            "offset_days": item.get("offset_days"),
            "relation": _relation(item.get("offset_days")),
        }
        # 因此必须给一个**确定性 tie-break**，否则同一份数据换个迭代顺序就换一行文案。
        key = (
            max(0, required - n),          # ① 还差多少样本：最少者优先（这就是"最接近"的定义）
            required,                      # ② 再比绝对门槛（|r| 更大者优先）
            str(item.get("x_label")),      # ③ 最后按标签固定次序，保证任何数据下都稳定
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
) -> tuple[list[dict], int, int]:
    """返回 (本次下发的结论, 实际检验了多少组, 达到可报告标准的条数, 未通过的原始条目)。

    第二个数是前端表达"这次一共检验了几组"的唯一来源——过滤之后
    `len(correlations)` 不再等于检验次数，用它去算会得出"0 组中 11 组跨 0"
    这种自相矛盾的句子。
    第三个数与 `include_hidden` 无关，始终是"通过校正的条数"。
    """
    found: list[dict] = []
    for x_key, y_key, x_label, y_label, offset in CORRELATION_PAIRS:
        xs: list[float] = []
        ys: list[float] = []
        for index, row in enumerate(series):
            target = index + offset
            if target >= len(series):
                break
            x_value = row.get(x_key)
            y_value = series[target].get(y_key)
            if x_value is None or y_value is None:
                continue
            xs.append(float(x_value))
            ys.append(float(y_value))
        if len(xs) < MIN_CORRELATION_SAMPLES:
            continue
        r = pearson(xs, ys)
        if r is None:
            continue
        slope = linear_slope(xs, ys)
        ci_low, ci_high = metrics.fisher_ci(r, len(xs))
        found.append(
            {
                "x": x_key,
                "y": y_key,
                "x_label": x_label,
                "y_label": y_label,
                "offset_days": offset,
                "relation": _relation(offset),
                "r": round(r, 3),
                "n": len(xs),
                "ci_low": ci_low,
                "ci_high": ci_high,
                # 强度两阶段判定，见下方 _strength 调用处的说明。
                "strength": None,
                "slope": round(slope, 5) if slope is not None else None,
            }
        )
    # 多重比较：分母固定用**预定义的检验总数** `len(CORRELATION_PAIRS)`，
    # 不用 `len(found)`。因为 `found` 会随数据量变化——样本不足的配对会被跳过，
    # 于是「数据越少 → 分母越小 → 校正越松 → 越容易显著」，形成反向偏置。
    # 而这类数据的缺失恰恰不是随机的（不戴表的日子本身就是信息），
    # 所以宁可保守：始终按跑满 12 次来校正。
    tests = max(1, len(CORRELATION_PAIRS))
    for item in found:
        ci_low, ci_high = item["ci_low"], item["ci_high"]
        item["excludes_zero"] = (
            ci_low is not None and ci_high is not None and (ci_low > 0 or ci_high < 0)
        )
        item["alpha_adjusted"] = round(CORRELATION_ALPHA / tests, 4)
        # 用同一 Fisher z 门槛近似判断“校正后仍显著”：需要 |r| 够大且 n 够大。
        item["survives_correction"] = bool(
            item["excludes_zero"]
            and metrics.correlation_is_significant(item["r"], item["n"], CORRELATION_ALPHA / tests)
        )
        item["strength"] = _strength(
            item["r"], ci_low, ci_high, survives=item["survives_correction"]
        )
    # 排序：按 CI 下界离 0 的距离（n 大、CI 窄的排前面），最后按 |r|。
    found.sort(
        key=lambda item: (
            abs(item["ci_low"]) if item["ci_low"] is not None else 0.0,
            abs(item["r"]),
        ),
        reverse=True,
    )
    # **只下发通过多重比较校正的结论。**
    # 过不了校正的相关系数不再返回——不是"显示出来再补一句不显著"。
    # 设计前提：不要给一份半成品、然后用防御性描述免责——
    # 要么给出能站住的结论，要么连数字一起不下发。
    # 判断留在这一处，前端直接渲染拿到的东西即可，不需要再判断可信度。
    tested = len(found)
    kept = [item for item in found if item["survives_correction"]]
    # 第二个返回值固定为"达到可报告标准的条数"，**不随 include_hidden 变化**：
    # `correlations_hidden` 回答的是"有多少条没过标准"，这是数据的属性，不是请求的属性。
    # 调试模式只是把未通过的那些也放回列表，不等于它们"没被隐藏"。
    if include_hidden:
        return found, tested, len(kept), found
    return kept, tested, len(kept), [item for item in found if not item["survives_correction"]]


# ---------- 周内 / 热图 / 基线带 ----------


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


def _band(series: list[dict], metric: str) -> list[dict]:
    """7 个日历日的滚动均值 ± 1 标准差（样本不足处为 null）。

    窗口按日历对齐（见 `_dense_window`），不是按行号——缺测的日子会让
    “最近 7 行”实际覆盖更长的时间，带宽会跟着变宽，看着像波动变大。
    """
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


def _achievements_summary(series: list[dict]) -> dict[str, Any]:
    """"近 N 天里达标几天"的权威计数（N = ACHIEVEMENTS_WINDOW_DAYS）。

    以前这两件事都在前端：`achievements.slice(-28)` 决定窗口、`.filter(r >= 1).length`
    数达标。窗口和算法都只有浏览器知道——后端测不到，调常量也没人会发现。

    `total` 是**窗口内实际存在的天数**（序列不足窗口时为序列长度），与前端
    `slice` 后取 `.length` 的口径一致，所以切过来数字不会变。
    """
    rows = series[-ACHIEVEMENTS_WINDOW_DAYS:]
    out: dict[str, Any] = {}
    for key in ("steps_ratio", "sleep_ratio"):
        achieved = sum(1 for row in rows if (row.get(key) or 0) >= 1)
        out[key] = {
            "window_days": ACHIEVEMENTS_WINDOW_DAYS,
            "total": len(rows),
            "achieved": achieved,
        }
    return out


# ---------- 恢复 / 负荷 / 节律 ----------


def _state_label(state: str) -> str:
    return {"good": t('server.insights._state_label.09dfac88c'), "ok": t('server.insights._state_label.296de0e31'), "low": t('server.insights._state_label.fd9b72d8d')}.get(state, "—")


def _swc_note(baseline: float | None, swc: float | None, unit: str) -> str:
    """把判读规则摊开说，并区分三种情况。

    原来这三个字段都写死成“对比 28 天基线”，但基线数值本身就是独立字段，
    这句话不提供信息、也说不清规则。而写成“样本不足”又会误报：基线有 28 个样本
    但全都一样时 SD=0，SWC 也是 0，属于“没有波动”而不是“样本不足”。
    """
    if baseline is None or swc is None:
        return t('server.insights._swc_note.db2718719')
    if swc == 0:
        return t('server.insights._swc_note.b661e5835')
    return t('server.insights._swc_note.6ed944b46', swc=swc, unit=unit)


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
            "label": t('server.insights._recovery.98439c55d'),
            "value": sleep_minutes,
            "unit": "min",
            "goal": need,
            "state": sleep_state,
            "state_label": _state_label(sleep_state),
            "note": t('server.insights._recovery.b8230baf0', p1=need / 60),
        },
        {
            "key": "efficiency",
            "label": t('server.insights._recovery.dc4a0545d'),
            "value": efficiency,
            "unit": "%",
            "baseline": eff_base,
            "state": eff_state,
            "state_label": _state_label(eff_state),
            # 原来三项都写“对比 28 天基线”，而基线数值本身已是独立字段，
            # 这句既不提供信息、也说不清判定规则。改成把规则说清。
            "note": t('server.insights._recovery.6c355cba8', EFFICIENCY_LOW_DROP=EFFICIENCY_LOW_DROP),
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
            "label": t('server.insights._recovery.e9a48bd3e'),
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
            level, level_label = "low", t('server.insights._recovery.fd9b72d8d')
        elif low == 0 and good >= 3:
            level, level_label = "high", t('server.insights._recovery.d56bfdd5c')
        elif low <= 1 and good >= 2:
            level, level_label = "good", t('server.insights._recovery.cfea0dce5')
        else:
            level, level_label = "ok", t('server.insights._recovery.91e25f4dd')
    else:
        level, level_label = "unknown", t('server.insights._recovery.84c6e0168')

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
    for day in days[-SHORT_WINDOW_DAYS:]:
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
        # 与 150 分钟比较时用加权值：直接将中等与高强度分钟相加会高估活动量。
        "who_weighted_minutes": round(weighted_week),
        "who_pct": round(weighted_week / WHO_WEEKLY_ACTIVE_MINUTES * 100) if weighted_values else None,
        "sedentary_avg_7": _mean(sedentary[-SHORT_WINDOW_DAYS:]),
        # 回显本块用的窗口，前端"近 7 天"那类文案由它渲染，不再写死字面量
        "window_days": SHORT_WINDOW_DAYS,
        "baseline_days": metrics.BASELINE_WINDOW_DAYS,
        "workout_minutes_week": round(workout_minutes),
    }


def _rhythm(days: list[dict]) -> dict[str, Any]:
    recent = days[-metrics.BASELINE_WINDOW_DAYS:]
    timing = metrics.sleep_timing(recent)
    skipped = metrics.sri(days[-(RHYTHM_WINDOW_DAYS + 1) :])
    hourly = metrics.hourly_activity(days[-RHYTHM_WINDOW_DAYS:])
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
        # 本块存在两个窗口，分别回显，避免前端把它们当成同一个"近 N 天"
        "rhythm_days": RHYTHM_WINDOW_DAYS,
        "baseline_days": metrics.BASELINE_WINDOW_DAYS,
        "hourly": hourly,
        "cosinor": cos,
    }


def _coverage(series: list[dict], days: list[dict], window_days: int) -> dict[str, Any]:
    """这份结论到底是基于多少数据算出来的。

    两件事分开说，因为它们的原因不同：
    - **缺天**：这几天本地根本没有存档（服务停过、没同步）；
    - **部分同步**：那天有存档，但某些接口失败（`Coverage.is_partial`），
      所以那一天看起来“有数据”，实际缺几项。

    多个小时的窗口里如果只有 22 天有效，所有基线/负荷/节律都是基于 22 天算的，
    必须在输出里说明，不能默认读的人会把 `window_days=30` 当成“基于 30 天”。
    """
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
    # 同一时间窗口内，各天的“有值指标个数”应当大致相当。某天明显更少
    # （比如只有 3 个而不是 6 个）通常意味着那天同步/解析不完整。
    #
    # **这个方法只能抓单天离群，抓不到均匀漂移。** 如果上游把某个字段改名，
    # 导致窗口内每一天都少同一项，中位数会跟着一起降，就没有任何离群点：
    # 实测每天从 5 项降到 4 项时，`typical_fields` 变 4、`count` 变 0，检测完全静默。
    # 中位数塌到 3 以下时才会被下一行的 `typical >= 3` 兜住，那属于极端情况。
    #
    # 均匀漂移需要**窗口之外的基线**才能发现（比对上次已存指标数），
    # 那在写入侧（`_sync_day`）做，不在这个纯函数的视野里。两者盲区不同，互补。
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
        # 大多数结论依赖这几项里最弱的那一项。只在本窗口**有数据**的指标里挑：
        # 某指标全程 0 天说明这条数据源根本没采，不是“缺天”，单独记进 absent_metrics。
        "weakest_metric": min(present, key=lambda key: present[key]) if present else None,
        "weakest_metric_days": min(present.values()) if present else 0,
    }


def _summary_table(series: list[dict]) -> list[dict]:
    specs = (
        ("sleep_minutes", t('server.insights._summary_table.98439c55d'), "min", 0),
        ("sleep_efficiency", t('server.insights._summary_table.dc4a0545d'), "%", 0),
        ("hrv_ms", "HRV", "ms", 1),
        ("resting_hr", t('server.insights._summary_table.e9a48bd3e'), "bpm", 0),
        ("steps", t('server.insights._summary_table.2986c7ee9'), t('server.insights._summary_table.a621b7cd7'), 0),
        ("active_minutes", t('server.insights._summary_table.301220d3d'), "min", 0),
        ("sedentary_minutes", t('server.insights._summary_table.aa1b957b8'), "min", 0),
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


_METRIC_LABELS = {
    "sleep_minutes": t('server.insights.labels.98439c55d'),
    "sleep_efficiency": t('server.insights.labels.dc4a0545d'),
    "resting_hr": t('server.insights.labels.e9a48bd3e'),
    "hrv_ms": "HRV",
    "steps": t('server.insights.labels.2986c7ee9'),
    "active_minutes": t('server.insights.labels.301220d3d'),
    "sedentary_minutes": t('server.insights.labels.52eaa93d2'),
}


def _facts(recovery: dict, load: dict, rhythm: dict, coverage: dict) -> list[str]:
    """给首页的几条要点。

    两条克制原则：
    - **先说事实，再说话可说清的解释**；解释只用「越高越规律」这类方向性说法，
      不编造经过验证的分级阈值（SRI 的文献分段与人群强相关，不适用于个人单点）。
    - **数据可信度类的提示排在最后**，不占前面最显眼的位置。
    """
    substantive: list[str] = []
    caveats: list[str] = []

    debt = recovery.get("sleep_debt") or {}
    if debt.get("nights", 0) >= 4 and debt.get("minutes", 0) >= 30:
        hours = debt["minutes"] / 60
        substantive.append(
            t('server.insights._facts.a8ab2ddad', p1=debt['window'], hours=hours, p3=debt['nights'])
        )

    jetlag = rhythm.get("social_jetlag_min")
    if jetlag is not None:
        substantive.append(t('facts.social_jetlag.small' if jetlag < 60 else 'facts.social_jetlag', jetlag=jetlag))

    if rhythm.get("sri") is not None:
        substantive.append(
            t('server.insights._facts.a1fe20b99', p1=rhythm['sri'], p2=rhythm['sri_pairs'])
        )

    cos = rhythm.get("cosinor") or {}
    if cos.get("acrophase_hour") is not None:
        substantive.append(
            t('server.insights._facts.719b341ce', p1=int(cos['acrophase_hour']), p2=cos['r2'])
        )

    if load.get("steps_ratio") is not None:
        substantive.append(
            t('server.insights._facts.f6d474d69', p1=load['steps_ratio'])
        )

    if load.get("mvpa_minutes_week"):
        weighted = load.get("who_weighted_minutes")
        pct = load.get("who_pct")
        key = 'facts.weekly_activity.near' if pct is not None and 100 <= pct <= 200 else (
            'facts.weekly_activity.above' if pct is not None and pct > 200 else 'facts.weekly_activity.below')
        substantive.append(t(key, weighted=weighted))

    archived = coverage.get("days_with_archive")
    weakest = coverage.get("weakest_metric_days")
    # 只差 1 天不值得占一条事实的位置；差 2 天以上或缺口超过 10% 才提。
    if archived and weakest is not None and weakest < archived and (
        archived - weakest >= 2 or weakest < archived * 0.9
    ):
        caveats.append(
            t('server.insights._facts.32dda8e94', archived=archived, p2=_METRIC_LABELS.get(coverage.get('weakest_metric'), t('server.insights._facts.91e454f17')), weakest=weakest)
        )

    excluded = coverage.get("excluded_out_of_range") or {}
    if excluded:
        detail = t('separator.list').join(t('server.insights._facts.3901996b4', p1=_METRIC_LABELS.get(key, key), value=value) for key, value in excluded.items())
        caveats.append(t('server.insights._facts.3930644c9', detail=detail))

    sparse = coverage.get("sparse_days") or {}
    if sparse.get("count"):
        dates = t('separator.list').join(d[-5:] for d in sparse["dates"][:3])
        caveats.append(
            t('server.insights._facts.7eddd966b', p1=sparse['count'], dates=dates)
        )

    if coverage.get("partial_days"):
        caveats.append(t('server.insights._facts.c3d984976', p1=coverage['partial_days']))

    return substantive + caveats


# ---------- 主入口 ----------


def build_insights(
    days: list[dict],
    goals: dict,
    *,
    end_date: str | None = None,
    window_days: int = INSIGHTS_DAYS_DEFAULT,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """汇总分析结果。

    `include_hidden` 仅供调试（`/api/v1/insights?include_hidden=1`）：
    默认只下发通过多重比较校正的相关结论，未通过的**不返回**，
    只以 `correlations_tested` / `correlations_hidden` 两个计数表达事实。
    打开后全部检验结果都会下发，每条带 `survives_correction`，供排查用。
    """
    valid_days = [day for day in (days or []) if isinstance(day, dict) and day.get("date")]
    series = _series(valid_days, goals or {})
    dates = [row["date"] for row in series]
    recovery = _recovery(series, valid_days, goals or {})
    load = _load(series, valid_days)
    rhythm = _rhythm(valid_days)
    coverage = _coverage(series, valid_days, window_days)
    weekday_rows = _weekday(series)
    correlations, correlations_tested, correlations_kept, correlations_rejected = _correlations(
        series, include_hidden=include_hidden
    )
    return {
        "from": dates[0] if dates else None,
        "to": end_date or (dates[-1] if dates else None),
        "window_days": window_days,
        "n_days": len(series),
        "series": series,
        "correlations": correlations,
        # 检验了几组 / 其中没过校正标准的几组。
        # **两个数都不随 include_hidden 变化**：它们描述数据本身，不描述本次请求。
        #
        # 注意这里有一处刻意的命名代价：调试模式（include_hidden=True）下
        # `correlations` 会把未通过的那些也一并发出去，而 `correlations_hidden` 仍 >0，
        # 看起来像"藏了东西其实没藏"。原因是它回答的是"有多少条没过标准"，
        # 不是"这个响应里少放了几条"。前者是可观测性要的答案
        # （弱结果会不会让内容越来越少），后者可以自己数出来。
        # 调试模式不对外，所以这处歧义只影响排查的人；前端只读默认模式。
        "correlations_tested": correlations_tested,
        "correlations_hidden": correlations_tested - correlations_kept,
        # 候选集必须去重：`include_hidden=True` 时 `correlations` 与
        # 只在调试模式下可见——正是那种“排查时才暴露”的错误。
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
                    # `slope` 只在通过校正时给：它同时是**画回归线的输入**和**解读句的原料**。
                    # 未通过的给 None，于是"能不能画线"由数据形状决定，
                    # 不靠前端记得再判一次 survives_correction（约定会漏，形状不会）。
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
        "band_outside": _band_outside(series),
        "bands_outside": _band_outside_by_metric(series),
        "heatmap": _heatmap(valid_days),
        "bands": {
            "resting_hr": _band(series, "resting_hr"),
            "hrv_ms": _band(series, "hrv_ms"),
            "sleep_minutes": _band(series, "sleep_minutes"),
            "steps": _band(series, "steps"),
            # 活动主页的趋势层需要活跃分钟的 ±1σ 带，与其余四项同构
            "active_minutes": _band(series, "active_minutes"),
        },
        "timing": _timing(series),
        "achievements": _achievements(series),
        "achievements_summary": _achievements_summary(series),
        # 全部窗口常量的唯一回显点：前端任何"近 N 天"都应指到这里，
        # 而不是在模板里写死。键名按**用途**而非常量名，前端读的是语义。
        # 只放**各区块内部**的窗口。请求窗口只有一个名字：顶层 `window_days`。
        # 同一个值给两个键名，就是"同一个词两个来源"——哪怕其中一个更整齐。
        "windows": {
            "band": BAND_WINDOW_DAYS,
            "short": SHORT_WINDOW_DAYS,
            "baseline": metrics.BASELINE_WINDOW_DAYS,
            "summary_trend": SUMMARY_TREND_DAYS,
            "achievements": ACHIEVEMENTS_WINDOW_DAYS,
            # 节律块内部同时用两个窗口：SRI/余弦拟合用 RHYTHM_WINDOW_DAYS，
            # 入睡-起床时段分布用 BASELINE_WINDOW_DAYS。两者不能合并成一个键。
            "rhythm": RHYTHM_WINDOW_DAYS,
        },
        "recovery": recovery,
        "load": load,
        "rhythm": rhythm,
        "coverage": coverage,
        "summary_table": _summary_table(series),
        "weekly": _weekly(series),
        "facts": _facts(recovery, load, rhythm, coverage),
    }
