"""可解释的健康指标计算：睡眠规律性（SRI）、社交时差、日活动余弦节律、
急慢性负荷比（ACWR）、最小有意义变化（SWC）与相关系数置信区间。

全部为纯函数，零外部依赖；输入是已存档的日数据 dict，输出为原始数值。
方法来源与**实现差异**（重要，别拿这些数去和论文数值直接比）：
- SRI: 思路源自 Phillips et al. 2017 / Windred et al. 2024（15 分钟睡眠-清醒状态一致率）。
  **注意：本实现是校正机会水平（chance-corrected）的 kappa 式版本，
  不是 Phillips 原文的线性重标定公式。两者在「完全一致 → 100」「与随机无异 → 0」
  这两个端点上一致，但中间段数值不同，不可与文献绝对值相减比较。**
- Social jetlag: Wittmann et al. 2006（工作日与自由日睡眠中点之差）
- Cosinor: 24 小时余弦拟合（MESOR / 振幅 / 峰值时刻 / R²）
- ACWR: 7 天急性负荷 / 28 天慢性负荷（Gabbett 2016 的运动负荷框架，此处用于活动分钟）。
  **窗口必须是按日历对齐的稠密序列（缺测填 None），见 `acwr()` 的说明。**
"""

from __future__ import annotations

from health_by.i18n import t

import math
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, time, timedelta
from statistics import mean, median, pstdev
from typing import Any, Iterable

from .regime import field_appears

def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mean_sd(values: Iterable[float]) -> tuple[float | None, float | None]:
    items = [v for v in values if v is not None]
    if not items:
        return None, None
    return mean(items), (pstdev(items) if len(items) > 1 else 0.0)


# ---------- 个人基线（全站唯一定义）----------
#
# 页面上一旦有两套「基线」，用户看到的两句结论就会互相冒充：一处说"比基线高 X"，
# 另一处说"偏离基线"，却无法知道是不是同一个基线。所以窗口、中心量、最小样本数
# 只在这里定义一次，analysis.py 与 insights.py 都引用它。
#
# 为什么是 28 天 + 中位数 + 最少 7 个样本：
# - 短窗口（如 7 天）均值的日间噪声大，容易得出"今天 HRV 偏低"这种站不住的结论；
# - 中位数比均值抗离群（一次通宵、一次发烧不该把基线拖走）；
# - 样本少于 7 时算出的标准差极不稳定，拿它的 0.5 倍当最小有意义变化（SWC）会误导。
BASELINE_WINDOW_DAYS = 28
BASELINE_MIN_SAMPLES = 7


def dense_window(
    series: list[dict], key: str, size: int, *, offset_days: int = 0
) -> list[float | None]:
    """按**日历**取窗口：以序列最后一行日期 − offset_days 为终点，向前 size 天。

    必须按日历而不是按行号：缺测的日子会让"最近 7 行"实际覆盖更长的时间，
    基线窗口跟着变长，两处用不同对齐方式算出的基线就会不一样。
    """
    dated = [(row.get("date"), row.get(key)) for row in series if row.get("date")]
    if not dated:
        return [None] * size
    by_date = {str(date_value): value for date_value, value in dated}
    try:
        end = date_type.fromisoformat(str(series[-1]["date"])) - timedelta(days=offset_days)
    except (KeyError, TypeError, ValueError):
        return [None] * size
    return [
        by_date.get((end - timedelta(days=back)).isoformat()) for back in range(size - 1, -1, -1)
    ]


def baseline_before(
    series: list[dict],
    key: str,
    *,
    window: int = BASELINE_WINDOW_DAYS,
    end_date: str | None = None,
    offset_days: int = 1,
) -> tuple[float | None, float | None]:
    """兼容入口：只要 (中位数, 标准差)，见 baseline_stats。"""
    stats = baseline_stats(series, key, window=window, end_date=end_date, offset_days=offset_days)
    return stats["median"], stats["spread"]


def baseline_stats(
    series: list[dict],
    key: str,
    *,
    window: int = BASELINE_WINDOW_DAYS,
    end_date: str | None = None,
    offset_days: int = 1,
) -> dict:
    """某一日之前的个人基线及其样本信息。

    返回中心量、离散度、样本数、窗口及有效起点；仅持续字段首现允许截短。
    - median/spread 为 None 表示样本不足（宁可说"样本不足"，也不要拿 2 个点算假基线）；
    - sample_count 是**真正进入计算**的有效样本数，供界面解释"为什么没有基线"。
    - break 保留字段但恒为 None：统计跳变检测未启用；insufficient_reason 说明样本不足。
    `end_date` 为空时以序列最后一天为基准日。`offset_days` 表示基准日要排除几天
    （默认 1：调用方传入的序列**包含当天**，基线必须排除当天，否则自己和自己比）。
    若调用方传入的序列本身已经不含当天，应传 0，否则会白白丢掉一天。
    """
    empty = {"median": None, "spread": None, "sample_count": 0,
             "window_days": window, "min_samples": BASELINE_MIN_SAMPLES,
             "valid_from": None, "break": None, "field_appearance": None,
             "insufficient_reason": t('server.metrics.baseline_stats.c3dc932c7')}
    anchor = str(end_date) if end_date else (str(series[-1].get("date")) if series else None)
    if not anchor:
        return empty
    try:
        end = date_type.fromisoformat(anchor) - timedelta(days=offset_days)
        start = end - timedelta(days=window - 1)
    except ValueError:
        return empty
    by_date = {str(row.get("date")): to_float(row.get(key)) for row in series
               if start.isoformat() <= str(row.get("date") or "") <= end.isoformat()}
    dated = sorted((day, value) for day, value in by_date.items() if value is not None)
    # 对物理上应为正数的四项，零可作为“无读数”证据；皮温差、行为量和其它字段的零保持有效。
    appearance = field_appears(sorted(by_date.items()), zero_is_missing=key in {
        "resting_hr", "hrv_ms", "breathing_rate", "spo2",
    })
    valid_from = max(start.isoformat(), appearance) if appearance else start.isoformat()
    values = [value for day, value in dated if day >= valid_from]
    context = {"valid_from": valid_from, "break": None, "field_appearance": appearance}
    if len(values) < BASELINE_MIN_SAMPLES:
        return {**empty, **context, "sample_count": len(values),
                "insufficient_reason": t('server.metrics.baseline_stats.3eff63c21', BASELINE_MIN_SAMPLES=BASELINE_MIN_SAMPLES, p2=len(values))}
    return {
        "median": round(median(values), 1),
        "spread": round(pstdev(values), 2),
        "sample_count": len(values),
        "window_days": window,
        "min_samples": BASELINE_MIN_SAMPLES,
        **context,
        "insufficient_reason": None,
    }


def total_active_minutes(light, moderate, vigorous) -> float | None:
    """全部活动分钟 = 轻 + 中 + 高。**派生值，不落库**（读时算，口径随代码走）。

    为什么需要它：`active_minutes` 在翻译层就是 `moderate + vigorous`，即 MVPA。
    页面上「活跃 X 分钟（中高强度 X 分钟）」会把同一个数印两遍，读者分不清是两个指标
    还是一个指标算了两次。

    语义（有意从严）：
    - **缺 `light` 时返回 None，不退化成等于 MVPA** —— 否则两个数又会相同，
      正是本函数要消除的问题；
    - `light` 有值、其余缺失时按 0 计，所以它是"已知分量之和"，不是精确总量。
    """
    if light is None:
        return None
    return (light or 0) + (moderate or 0) + (vigorous or 0)


def swc(values: Iterable[float]) -> float | None:
    """最小有意义变化：0.5 × 基线标准差（Hopkins 的 SWC 约定）。"""
    _, spread = mean_sd(values)
    return round(0.5 * spread, 3) if spread is not None else None


_R_CLAMP = 0.999999


def fisher_ci(r: float | None, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    """相关系数的 Fisher z 变换 95% 置信区间。

    `|r| = 1` 时 `atanh` 发散，原先直接返回 `(None, None)`；但那样一来
    **完美相关反而没有置信区间**，下游"CI 不跨 0"的判定就为假，
    于是最该报出来的结论会被当成证据不足丢掉。改为把 `|r|` 夹到 0.999999 再算，
    CI 会退化成贴着 ±1 的一个极窄区间，语义正确。
    """
    if r is None or n <= 3:
        return None, None
    z_value = math.atanh(max(-_R_CLAMP, min(_R_CLAMP, r)))
    se = 1 / math.sqrt(n - 3)
    lo = math.tanh(z_value - z * se)
    hi = math.tanh(z_value + z * se)
    return round(max(-1.0, lo), 3), round(min(1.0, hi), 3)


MIN_COSINOR_R2 = 0.3


def normal_quantile(p: float) -> float:
    """标准正态分位数（双尾 p/2 用），二分法实现，避免引入 scipy。"""
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    target = p
    low, high = -10.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        cdf = 0.5 * (1 + math.erf(mid / math.sqrt(2)))
        if cdf < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def correlation_is_significant(r: float | None, n: int, alpha: float = 0.05) -> bool:
    """相关系数是否显著（Fisher z 检验，双尾）。

    注意：`alpha` 应当已经做过多重比较校正（Bonferroni 用 α/检验次数）。
    """
    if r is None or n <= 3:
        return False
    # 同样处理 |r| = 1：完美相关是最强证据，不是"无法判定"。
    z_value = abs(math.atanh(max(-_R_CLAMP, min(_R_CLAMP, r)))) * math.sqrt(n - 3)
    return z_value > normal_quantile(1 - alpha / 2)


def strength_label(r: float | None) -> str:
    if r is None:
        return "—"
    magnitude = abs(r)
    if magnitude >= 0.6:
        return t('server.metrics.strength_label.f038053be')
    if magnitude >= 0.4:
        return t('server.metrics.strength_label.a567bdaa1')
    if magnitude >= 0.2:
        return t('server.metrics.strength_label.defa376ea')
    return t('server.metrics.strength_label.1d002ae82')


# ---------- 睡眠规律性 ----------


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sleep_bins_by_date(days: list[dict], bin_minutes: int = 15) -> dict[str, list[bool]]:
    """把睡眠阶段时间线拆成按日历日、每 15 分钟一格的睡眠状态。

    跨夜段按午夜切开，分别记到两天；缺失时间线的日期不会凭空生成。
    """
    per_day: dict[str, list[bool]] = defaultdict(lambda: [False] * (24 * 60 // bin_minutes))
    for day in days:
        sleep = day.get("sleep") or {}
        for segment in sleep.get("stage_timeline") or []:
            if segment.get("stage") == "wake":
                continue
            start = _parse_iso(segment.get("start"))
            end = _parse_iso(segment.get("end"))
            if start is None or end is None or end <= start:
                continue
            cursor = start
            while cursor < end:
                midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo)
                chunk_end = min(end, midnight)
                start_minute = cursor.hour * 60 + cursor.minute + cursor.second / 60
                end_minute = (
                    (chunk_end - datetime.combine(cursor.date(), time.min, tzinfo=cursor.tzinfo)).total_seconds() / 60
                )
                bins = per_day[cursor.date().isoformat()]
                first = int(start_minute // bin_minutes)
                last = int(math.ceil(end_minute / bin_minutes))
                for index in range(max(0, first), min(len(bins), last)):
                    bins[index] = True
                cursor = chunk_end
    return dict(per_day)


def sri(days: list[dict], *, bin_minutes: int = 15, max_pairs: int = 28) -> dict[str, Any]:
    """睡眠规律指数（SRI）：相邻两天同一时刻处于相同状态的比例，按机会水平校正。

    与文献的差异见模块 docstring：这是 kappa 式校正版本，不是 Phillips 原始的
    `-100 + 200 × agree/total`。端点一致（完全规律 100 / 与随机无异 0），中间段不可直接比。

    100 = 完全一致；0 = 与随机无异；负值 = 比随机更不一致。
    """
    bins_by_date = sleep_bins_by_date(days, bin_minutes)
    dates = sorted(bins_by_date)
    pairs: list[tuple[str, str]] = []
    for earlier, later in zip(dates, dates[1:]):
        if (date_type.fromisoformat(later) - date_type.fromisoformat(earlier)).days == 1:
            pairs.append((earlier, later))
    pairs = pairs[-max_pairs:]
    if not pairs:
        return {"value": None, "pairs": 0, "sleep_fraction": None}

    same = 0
    compared = 0
    sleep_bins = 0
    for earlier, later in pairs:
        left = bins_by_date[earlier]
        right = bins_by_date[later]
        same += sum(1 for a, b in zip(left, right) if a == b)
        sleep_bins += sum(left) + sum(right)
        compared += len(left)
    if compared == 0:
        return {"value": None, "pairs": len(pairs), "sleep_fraction": None}
    fraction = sleep_bins / (2 * compared)
    chance = fraction**2 + (1 - fraction) ** 2
    value = 100 * (same / compared - chance) / (1 - chance) if chance < 1 else 0.0
    return {
        "value": round(value, 1),
        "pairs": len(pairs),
        "sleep_fraction": round(fraction, 3),
    }


def sleep_timing(days: list[dict]) -> dict[str, Any]:
    """入睡/起床/睡眠中点的分布，以及工作日与自由日的社交时差。"""
    midpoints: list[float] = []
    beds: list[float] = []
    wakes: list[float] = []
    work: list[float] = []
    free: list[float] = []
    for day in days:
        sleep = day.get("sleep") or {}
        start = _parse_iso(sleep.get("start_time"))
        end = _parse_iso(sleep.get("end_time"))
        date_value = day.get("date")
        if start is None or end is None or not date_value:
            continue
        bed = start.hour + start.minute / 60
        if bed < 12:
            bed += 24
        wake = end.hour + end.minute / 60
        if wake < 12:
            wake += 24
        midpoint = (bed + wake) / 2
        if midpoint >= 36:
            midpoint -= 24
        beds.append(bed)
        wakes.append(wake)
        midpoints.append(midpoint)
        try:
            weekday = date_type.fromisoformat(str(date_value)).weekday()
        except ValueError:
            continue
        (free if weekday >= 5 else work).append(midpoint)

    def spread(values: list[float]) -> float | None:
        if len(values) < 3:
            return None
        return round(pstdev(values) * 60)

    social_jetlag = None
    if len(work) >= 3 and len(free) >= 2:
        social_jetlag = round(abs(mean(free) - mean(work)) * 60)

    return {
        "midpoint_mean": round(mean(midpoints), 2) if midpoints else None,
        "midpoint_sd_min": spread(midpoints),
        "bed_sd_min": spread(beds),
        "wake_sd_min": spread(wakes),
        "social_jetlag_min": social_jetlag,
        "n_nights": len(midpoints),
    }


def sleep_debt(days: list[dict], need_minutes: float = 480, window: int = 7) -> dict[str, Any]:
    """最近 N 天的累计睡眠缺口（仅统计有睡眠记录的夜晚，并回报覆盖天数）。"""
    recent = days[-window:]
    deficits: list[float] = []
    total = 0.0
    for day in recent:
        minutes = to_float((day.get("sleep") or {}).get("total_minutes"))
        if minutes is None:
            continue
        deficit = max(0.0, need_minutes - minutes)
        deficits.append(deficit)
        total += deficit
    return {
        "minutes": round(total),
        "nights": len(deficits),
        "window": window,
        "avg_deficit": round(total / len(deficits)) if deficits else None,
    }


# ---------- 活动节律 ----------


def hourly_activity(days: list[dict], hours: int = 24) -> list[float | None]:
    totals = [0.0] * hours
    counts = [0] * hours
    for day in days:
        for point in (day.get("activity") or {}).get("steps_intraday") or []:
            raw_time = str(point.get("time") or "")
            if len(raw_time) < 2 or not raw_time[:2].isdigit():
                continue
            hour = int(raw_time[:2])
            value = to_float(point.get("value"))
            if value is None or not (0 <= hour < hours):
                continue
            totals[hour] += value
            counts[hour] += 1
    return [round(totals[h] / counts[h]) if counts[h] else None for h in range(hours)]


def _solve3(a: list[list[float]], b: list[float]) -> list[float] | None:
    """解 3×3 线性方程组（列主元高斯消元）。奇异时返回 None。"""
    m = [row[:] + [value] for row, value in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(m[row][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for row in range(col + 1, 3):
            factor = m[row][col] / m[col][col]
            for k in range(col, 4):
                m[row][k] -= factor * m[col][k]
    out = [0.0, 0.0, 0.0]
    for row in reversed(range(3)):
        out[row] = (m[row][3] - sum(m[row][k] * out[k] for k in range(row + 1, 3))) / m[row][row]
    return out


def cosinor(hourly: list[float | None]) -> dict[str, Any]:
    """24 小时余弦拟合：MESOR、振幅、峰值时刻（小时）与拟合优度 R²。

    用**带截距的最小二乘**（设计矩阵 `[1, cos, sin]`）拟合，而不是等间隔采样下的
    简化公式 `b = 2/n Σ y·cos`。原因：分时步数经常只有白天几个小时有值
    （夜间未佩戴/未记录），等间隔公式在这种缺测下会严重低估拟合质量。

    实测反例：真值是一条完美余弦（峰值 15:00），只保留 8–22 时的观测，
    简化公式给出 R² = **-7.77**，最小二乘给出 R² = **1.00**。
    由于 `acrophase_hour` 现在以 R² 为门槛，用简化公式会把完全可用的节律判成噪声。
    """
    points = [(hour, value) for hour, value in enumerate(hourly) if value is not None]
    if len(points) < 12:
        return {"mesor": None, "amplitude": None, "acrophase_hour": None, "r2": None, "usable": False}
    values = [value for _, value in points]
    design = [
        [1.0, math.cos(2 * math.pi * hour / 24), math.sin(2 * math.pi * hour / 24)]
        for hour, _ in points
    ]
    normal = [
        [sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)
    ]
    rhs = [sum(row[i] * value for row, value in zip(design, values)) for i in range(3)]
    beta = _solve3(normal, rhs)
    if beta is None:
        return {"mesor": None, "amplitude": None, "acrophase_hour": None, "r2": None, "usable": False}
    mesor, cos_coef, sin_coef = beta
    fitted = [row[0] * mesor + row[1] * cos_coef + row[2] * sin_coef for row in design]
    ss_res = sum((value - estimate) ** 2 for value, estimate in zip(values, fitted))
    # 总平方和必须相对**样本均值 ȳ**，不是拟合出的截距 mesor。
    #
    # 为什么这不是"两种都行、看习惯"：`Σ(y-c)²` 是中心 c 的函数，而它在 c = ȳ 处取最小值。
    # 所以对**任意**数据恒有 SS_tot(mesor) ≥ SS_tot(ȳ)，而 SS_res 用的是同一个拟合、不变，
    # 于是 R²(ȳ) ≤ R²(mesor) 结构上必然成立——用 mesor 只会让 R² 偏高，永远不会偏低。
    # 也就是说 MIN_COSINOR_R2 这道门会被系统性地放宽，不是偶尔放宽。
    #
    # 这个偏差只在缺测时出现：24 小时全覆盖且等间隔时，cos/sin 两列与常数列正交，
    # mesor == ȳ，两者完全相等；只观测白天时两列不再零均值，两者才分道扬镳。
    # 实测一组 mesor=101.7、ȳ=111.6 的弱节律：以 mesor 计 R²=0.464，以均值计 0.345，
    # 前者刚好越过 0.3 的门槛、后者越不过——被放宽掉的正是本该拦住的弱节律。
    #
    # 边界：窗口内所有值完全相同时 SS_tot(ȳ) = 0，此处返回 r2 = None（判为不可用），
    # 而不是像以 mesor 计时那样给出一个有定义但无意义的数。
    value_mean = sum(values) / len(values)
    ss_tot = sum((value - value_mean) ** 2 for value in values)
    amplitude = math.hypot(cos_coef, sin_coef)
    phase = math.atan2(sin_coef, cos_coef)
    r2 = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None
    # R² 低于阈值（含负值）时，拟合不比一条平线更好，峰值时刻没有意义。
    usable = r2 is not None and r2 >= MIN_COSINOR_R2
    return {
        "mesor": round(mesor),
        "amplitude": round(amplitude),
        "acrophase_hour": round((phase * 24 / (2 * math.pi)) % 24, 1) if usable else None,
        "r2": r2,
        "usable": usable,
        "hours_observed": len(points),
    }


def acwr(values: list[float | None], *, acute: int = 7, chronic: int = 28) -> dict[str, Any]:
    """急慢性负荷比：最近 `acute` 天均值 / 最近 `chronic` 天均值。

    **输入必须是按日历对齐的稠密序列**：一天一个元素，缺测填 `None`。
    函数按**位置**开窗（不用 `[: -acute]` 这类剔除空值后的切片），
    否则缺测会把「最近 7 天」拉长成最近十几个日历天，急性与慢性窗口不对齐，比值失真。

    窗口内有效样本不足（少于 `chronic` 天的一半）时返回 `None`，不硬算。
    """
    if len(values) < chronic:
        return {"acute": None, "chronic": None, "ratio": None}
    acute_window = values[-acute:]
    chronic_window = values[-chronic:]
    acute_clean = [v for v in acute_window if v is not None]
    chronic_clean = [v for v in chronic_window if v is not None]
    if len(chronic_clean) < chronic // 2 or len(acute_clean) < acute // 2:
        return {"acute": None, "chronic": None, "ratio": None}
    acute_mean = mean(acute_clean)
    chronic_mean = mean(chronic_clean)
    if chronic_mean <= 0:
        return {"acute": round(acute_mean, 1), "chronic": round(chronic_mean, 1), "ratio": None}
    return {
        "acute": round(acute_mean, 1),
        "chronic": round(chronic_mean, 1),
        "ratio": round(acute_mean / chronic_mean, 2),
        "acute_n": len(acute_clean),
        "chronic_n": len(chronic_clean),
    }
