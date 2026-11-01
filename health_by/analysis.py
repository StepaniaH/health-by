"""分析层：个人基线对比、周期差值、覆盖与新鲜度、规则化头条与洞察。

设计原则（沿用调研结论）：
- 不做复合评分、不做诊断；
- 每个结论必须可追溯到测量值、用户目标或可见个人基线；
- 单一夜间读数只描述、不下结论。
"""

from __future__ import annotations

from health_by.i18n import t

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable

from .util import iso_today, parse_utc

# 基线口径全站唯一，定义在 metrics.py（28 天 / 中位数 / 最少 7 样本）。
# 这里不再自带窗口常量：同一个词不能有两个答案。
from . import metrics
STALE_AFTER_HOURS = 6
MIN_PERIOD_SAMPLES = 3
COMPARISON_FIELDS = {
    "steps": ("activity", "steps"), "sleep_minutes": ("sleep", "total_minutes"),
    "resting_hr": ("heart", "resting_hr"), "hrv_ms": ("heart", "hrv_ms"),
    "breathing_rate": ("heart", "breathing_rate"), "spo2": ("heart", "spo2"),
    "skin_temp_delta": ("heart", "skin_temp_delta_c"),
}


def comparison_values(day: dict) -> dict:
    return {key: (day.get(group) or {}).get(field) for key, (group, field) in COMPARISON_FIELDS.items()}


# ---------- 基础统计 ----------


def compare_with_baseline(
    trends: list[dict[str, Any]],
    selected_date: str,
    current: float | int | None,
    selector: Callable[[dict], float | int | None],
    key: str | None = None,
) -> dict[str, Any]:
    """当前值 vs 个人基线（口径见 metrics.baseline_before：28 天 / 中位数 / 最少 7 样本）。

    以前这里用"前 7 天均值、无最小样本下限"，与 insights 侧的基线口径不同，
    同一个页面上会出现两个不同的"基线"。现在两边共用 metrics 的唯一定义。
    """
    prior = [point for point in trends if str(point.get("date") or "") < selected_date]
    metric_key = key or "value"
    series = [
        {"date": point.get("date"), metric_key: selector(point)}
        for point in prior
        if point.get("date")
    ]
    # 显式按所选日锚定并排除当天，避免昨日缺测时整个基线窗口向前漂移。
    stats = metrics.baseline_stats(series, metric_key, end_date=selected_date)
    baseline = stats["median"]
    change = baseline_change(current, baseline)
    return {
        "current": current,
        "baseline": round(baseline, 2) if baseline is not None else None,
        **change,
        "sample_count": stats["sample_count"],
        "window_days": metrics.BASELINE_WINDOW_DAYS,
        "min_samples": metrics.BASELINE_MIN_SAMPLES,
        "valid_from": stats["valid_from"],
        "break": stats["break"],
        "field_appearance": stats["field_appearance"],
        "insufficient_reason": stats["insufficient_reason"],
    }


def baseline_change(current, baseline) -> dict:
    """单日比较与双口径出口共用同一差额/百分比算法。"""
    difference = percent = None
    if current is not None and baseline is not None:
        difference = round(float(current) - baseline, 2)
        if baseline != 0:
            percent = round((float(current) - baseline) / baseline * 100, 1)
    return {"difference": difference, "percent_change": percent}


def period_delta(values: list[float | int | None], recent: int = 7) -> float | None:
    """近 N 天 vs 前 N 天的均值变化率（%）；每段至少 3 个有效点。"""
    prior = [float(v) for v in values[-2 * recent : -recent] if v is not None]
    latest = [float(v) for v in values[-recent:] if v is not None]
    if len(prior) < MIN_PERIOD_SAMPLES or len(latest) < MIN_PERIOD_SAMPLES:
        return None
    prior_mean = mean(prior)
    if prior_mean == 0:
        return None
    return round((mean(latest) - prior_mean) / prior_mean * 100, 1)


def _ratio(current: float | int | None, goal: float | int | None) -> float | None:
    if current is None or goal in (None, 0):
        return None
    return round(float(current) / float(goal), 3)


def _minutes_text(minutes: float) -> str:
    absolute = abs(int(round(minutes)))
    hours, remainder = divmod(absolute, 60)
    if not hours:
        return t('server.analysis._minutes_text.133c73c86', remainder=remainder)
    if not remainder:
        return t('server.analysis._minutes_text.2842db03b', hours=hours)
    return t('server.analysis._minutes_text.cde25540b', hours=hours, remainder=remainder)


# ---------- 主分析 ----------


def build_analysis(
    day: dict[str, Any],
    trends: list[dict[str, Any]],
    goals: dict[str, Any],
    *,
    now: datetime | None = None,
    display_values: dict[str, float | None] | None = None,
    display_modes: dict[str, str] | None = None,
) -> dict[str, Any]:
    selected = str(day.get("date") or "")
    activity = day.get("activity") or {}
    heart = day.get("heart") or {}
    sleep = day.get("sleep") or {}
    alerts = day.get("alerts") or {}
    coverage = day.get("coverage") or {}
    device = day.get("device") or {}

    # 先选展示口径，再生成差额、标题和解读；原始日值单独保留以供依据引用。
    comparisons = {}
    for key, daily in comparison_values(day).items():
        current = display_values.get(key, daily) if display_values is not None else daily
        comparisons[key] = {
            **compare_with_baseline(trends, selected, current, lambda point, metric=key: point.get(metric), key=key),
            "current_daily": daily,
        }

    steps_goal = goals.get("steps")
    sleep_goal = goals.get("sleep_minutes")
    goal_progress = {
        "steps": {
            "value": activity.get("steps"),
            "goal": steps_goal,
            "ratio": _ratio(activity.get("steps"), steps_goal),
        },
        "sleep_minutes": {
            "value": sleep.get("total_minutes"),
            "goal": sleep_goal,
            "ratio": _ratio(sleep.get("total_minutes"), sleep_goal),
        },
    }

    period_deltas = {
        "steps_7d": period_delta([p.get("steps") for p in trends]),
        "sleep_7d": period_delta([p.get("sleep_minutes") for p in trends]),
    }

    endpoint_count = int(coverage.get("endpoint_count") or 0)
    success_count = int(coverage.get("success_count") or 0)
    is_partial = success_count < endpoint_count
    errors = coverage.get("errors") or []

    now_dt = now or datetime.now(timezone.utc)
    stale = False
    freshness_at = device.get("last_sync_time") or day.get("generated_at")
    if selected == iso_today() and freshness_at:
        try:
            stale = (now_dt.astimezone(timezone.utc) - parse_utc(str(freshness_at))).total_seconds() > (
                STALE_AFTER_HOURS * 3600
            )
        except ValueError:
            stale = False

    sync = {
        "succeeded": success_count,
        "total": endpoint_count,
        "is_partial": is_partial,
        "errors": errors,
        "last_device_sync_at": device.get("last_sync_time"),
        "is_stale": stale,
    }

    headline = _headline(
        comparisons=comparisons,
        goal_progress=goal_progress,
        sync=sync,
        alerts=alerts,
        has_sleep=bool(sleep),
        display_modes=display_modes,
    )
    insights = _insights(comparisons, period_deltas, day, display_modes)

    return {
        "headline": headline,
        "comparisons": comparisons,
        "goal_progress": goal_progress,
        "period_deltas": period_deltas,
        "sync": sync,
        "insights": insights,
    }


# ---------- 头条（优先级：告警 > 数据新鲜度 > 覆盖 > 睡眠欠账 > 心率变化 > 达标 > 平稳）----------


def _headline(
    *,
    comparisons: dict[str, dict],
    goal_progress: dict[str, dict],
    sync: dict[str, Any],
    alerts: dict[str, Any],
    has_sleep: bool,
    display_modes: dict[str, str] | None = None,
) -> dict[str, str]:
    irregular = alerts.get("irregular_rhythm_alerts")
    sleep_goal = goal_progress.get("sleep_minutes") or {}
    steps_goal = goal_progress.get("steps") or {}
    resting = comparisons.get("resting_hr") or {}

    if isinstance(irregular, int) and irregular > 0:
        return {
            "tone": "alert",
            "eyebrow": t('server.analysis._headline.0153bdbee'),
            "title": t('server.analysis._headline.f75b00d62'),
            "body": t('server.analysis._headline.62b0b8f83'),
        }
    if sync.get("is_stale"):
        return {
            "tone": "warn",
            "eyebrow": t('server.analysis._headline.2188b98aa'),
            "title": t('server.analysis._headline.a8550e1d3'),
            "body": t('server.analysis._headline.d4d99f331'),
        }
    if sync.get("is_partial"):
        return {
            "tone": "warn",
            "eyebrow": t('server.analysis._headline.027f76fd6'),
            "title": t('server.analysis._headline.fc65911d2'),
            # 原来的“已显示的区块仍然可用”是安慰式空话：用户看得到区块在，不需要被告知。
            # 换成他能用的信息——具体少读了哪几项。
            "body": _partial_body(sync),
        }
    sleep_value = sleep_goal.get("value")
    sleep_target = sleep_goal.get("goal")
    if (
        has_sleep
        and isinstance(sleep_value, (int, float))
        and isinstance(sleep_target, (int, float))
        and sleep_value - sleep_target <= -60
    ):
        return {
            "tone": "warn",
            "eyebrow": t('server.analysis._headline.3589c1516'),
            "title": t('server.analysis._headline.15a9081d5'),
            "body": (
                t('server.analysis._headline.1840a5c0a', p1=_minutes_text(sleep_value), p2=_minutes_text(sleep_target - sleep_value), p3=_minutes_text(sleep_target))
            ),
        }
    if (
        resting.get("difference") is not None
        and resting.get("sample_count", 0) >= 4
        and resting["difference"] >= 4
    ):
        return {
            "tone": "warn",
            "eyebrow": t('server.analysis._headline.3a38fe779'),
            "title": t('server.analysis._headline.cd1478f75'),
            "body": t('server.analysis._headline.5b8da7ebd', p1=_current_label(display_modes, 'resting_hr'), p2=resting['sample_count'], p3=round(resting['difference'])),
        }
    steps_ratio = steps_goal.get("ratio")
    if isinstance(steps_ratio, float) and steps_ratio >= 1:
        return {
            "tone": "ok",
            "eyebrow": t('server.analysis._headline.521a53f75'),
            "title": t('server.analysis._headline.c10b73325'),
            "body": t('server.analysis._headline.a79e2eee1', p1=round(steps_ratio * 100)),
        }
    return {
        "tone": "ok",
        "eyebrow": t('server.analysis._headline.fea405f9b'),
        "title": t('server.analysis._headline.002422bde'),
        "body": _baseline_body(comparisons, display_modes),
    }


# ---------- 洞察卡 ----------


def _insight(identifier: str, tone: str, title: str, body: str) -> dict[str, str]:
    return {"id": identifier, "tone": tone, "title": title, "body": body}


def _partial_body(sync: dict[str, Any]) -> str:
    """部分覆盖时说明缺了哪几项，而不是笼统安慰一句。"""
    succeeded = sync.get("succeeded")
    total = sync.get("total")
    head = t('server.analysis._partial_body.ad978e1b2', succeeded=succeeded, total=total)
    errors = sync.get("errors") or []
    names: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            label = item.get("job") or item.get("endpoint") or item.get("name")
            if label:
                names.append(str(label))
        elif item:
            names.append(str(item))
    if not names:
        return head + t('sentence.end')
    shown = t('separator.list').join(names[:3])
    more = t('server.analysis._partial_body.0c5208b7b', p1=len(names)) if len(names) > 3 else ""
    return t('server.analysis._partial_body.26e76a6b3', head=head, shown=shown, more=more)


def _current_label(modes: dict[str, str] | None, key: str) -> str:
    return t('server.analysis._current_label.45285bc2d') if (modes or {}).get(key) == "rolling7" else t('server.analysis._current_label.96ad1e31a')


def _baseline_body(comparisons: dict[str, dict], display_modes: dict[str, str] | None = None) -> str:
    """平铺兜底头条的正文：报“实际比了什么”，不写“继续保持”这类空话。

    只列有读数、且基线样本数 >= 1 的项；一项都没有时明确说没说可比的项。
    """
    labels = {
        "steps": (t('server.analysis._baseline_body.2986c7ee9'), t('server.analysis._baseline_body.e010e3f57'), 0),
        "sleep_minutes": (t('server.analysis._baseline_body.01324e540'), t('server.analysis._baseline_body.40de54344'), 0),
        "resting_hr": (t('server.analysis._baseline_body.e9a48bd3e'), " bpm", 0),
        "hrv_ms": ("HRV", " ms", 0),
    }
    parts: list[str] = []
    for key, (label, unit, digits) in labels.items():
        item = comparisons.get(key) or {}
        current = item.get("current")
        samples = item.get("sample_count") or 0
        if current is None or samples < 1:
            continue
        shown = f"{float(current):g}" if (display_modes or {}).get(key) == "rolling7" else str(round(float(current), digits))
        parts.append(t('server.analysis._baseline_body.1fd48a48d', label=label, p2=_current_label(display_modes, key), shown=shown, unit=unit, samples=samples))
    if not parts:
        return t('server.analysis._baseline_body.d3c622ac8')
    return t('server.analysis._baseline_body.16bc10048', p1=t('separator.list').join(parts))


def _delta_text(comparison: dict[str, Any], unit: str, decimals: int = 1) -> str | None:
    difference = comparison.get("difference")
    if difference is None:
        return None
    sign = "+" if difference > 0 else "−"
    return f"{sign}{abs(round(float(difference), decimals))} {unit}"


def _insights(
    comparisons: dict[str, dict],
    period_deltas: dict[str, float | None],
    day: dict[str, Any],
    display_modes: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []

    hrv = comparisons.get("hrv_ms") or {}
    hrv_delta = _delta_text(hrv, "ms")
    if hrv_delta and hrv.get("sample_count", 0) >= 3:
        insights.append(
            _insight(
                "hrv",
                "info",
                t('server.analysis._insights.2d0817bd0'),
                t('server.analysis._insights.94efde139', p1=_current_label(display_modes, 'hrv_ms'), p2=hrv.get('current'), hrv_delta=hrv_delta, p4=hrv.get('sample_count')),
            )
        )

    spo2 = comparisons.get("spo2") or {}
    if spo2.get("current") is not None:
        text = f"{_current_label(display_modes, 'spo2')} {spo2.get('current')}%"
        delta = _delta_text(spo2, t('server.analysis._insights.05e23c0c1'))
        if delta and spo2.get("sample_count", 0) >= 3:
            text += t('server.analysis._insights.bab1485f4', delta=delta)
        insights.append(_insight("spo2", "info", t('server.analysis._insights.2836400cc'), text + t('sentence.end')))

    breathing = comparisons.get("breathing_rate") or {}
    if breathing.get("current") is not None and breathing.get("sample_count", 0) >= 3:
        delta = _delta_text(breathing, t('server.analysis._insights.58c5c5108'))
        body = t('server.analysis._insights.a83950342', p1=_current_label(display_modes, 'breathing_rate'), p2=breathing.get('current'))
        if delta:
            body += t('server.analysis._insights.bab1485f4', delta=delta)
        insights.append(_insight("breathing", "info", t('server.analysis._insights.f3261e58d'), body + t('sentence.end')))

    skin = comparisons.get("skin_temp_delta") or {}
    if skin.get("current") is not None:
        insights.append(
            _insight(
                "skin_temp",
                "info",
                t('server.analysis._insights.fde263b56'),
                f"{_current_label(display_modes, 'skin_temp_delta')} {skin.get('current')}°C",
            )
        )

    steps_delta = period_deltas.get("steps_7d")
    if steps_delta is not None:
        direction = t('server.analysis._insights.bce53fe72') if steps_delta >= 0 else t('server.analysis._insights.43c9f2ba7')
        insights.append(
            _insight("steps_period", "ok", t('server.analysis._insights.0f009e7ff'), t('server.analysis._insights.5a794d46e', direction=direction, p2=abs(steps_delta)))
        )
    sleep_delta = period_deltas.get("sleep_7d")
    if sleep_delta is not None:
        direction = t('server.analysis._insights.bce53fe72') if sleep_delta >= 0 else t('server.analysis._insights.43c9f2ba7')
        insights.append(
            _insight("sleep_period", "ok", t('server.analysis._insights.d4ccd9507'), t('server.analysis._insights.5a794d46e', direction=direction, p2=abs(sleep_delta)))
        )

    naps_count = day.get("naps_count")
    if naps_count:
        insights.append(
            _insight("naps", "ok", t('server.analysis._insights.8ffb0e1ac'), t('server.analysis._insights.cfe9b31b4', naps_count=naps_count, p2=day.get('naps_minutes')))
        )

    return insights
