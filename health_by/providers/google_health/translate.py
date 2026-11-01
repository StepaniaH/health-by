"""Google Health API v4 原始载荷 → DaySnapshot 翻译层。

容错原则：对 v4 响应的各种形状保持宽容（字段缺失/类型意外都不 crash）；
对调研阶段确认过的 API 怪癖做显式处理（见各注释与 docs/data-coverage.md）。
"""

from __future__ import annotations

from health_by.i18n import t

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ...models import (
    ActivityDay,
    BodyDay,
    Coverage,
    DaySnapshot,
    Device,
    HealthAlerts,
    HeartDay,
    Profile,
    SleepSession,
    StageSegment,
    TimePoint,
    Workout,
)
from ..base import RawDay

# ---------- 解析失败记录 ----------

MAX_PARSE_ISSUES = 20

# 用 ContextVar 而不是模块级列表：同步是并发的，多个 worker 会同时翻译不同的天。
_PARSE_ISSUES: ContextVar[list[dict] | None] = ContextVar("parse_issues", default=None)


def _note_parse_failure(helper: str, value: Any) -> None:
    """记录「拿到了值，但读不出来」。

    注意与「上游本来就没这个字段」区分：只有 value 非 None 时才记，
    否则每个缺失字段都会变成噪音，反而看不出真问题。
    """
    issues = _PARSE_ISSUES.get()
    if issues is None or len(issues) >= MAX_PARSE_ISSUES:
        return
    text = repr(value)
    issues.append({"helper": helper, "value": text[:120]})


# ---------- 基础解析 ----------


def _float(value: Any, *, transform: Callable[[float], float] | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        _note_parse_failure("_float", value)
        return None
    return transform(number) if transform else number


def _int(value: Any) -> int | None:
    number = _float(value)
    return None if number is None else int(round(number))


def _num1(value: Any) -> float | None:
    number = _float(value)
    return None if number is None else round(number, 1)


def _num2(value: Any) -> float | None:
    number = _float(value)
    return None if number is None else round(number, 2)


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _date_from_civil(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, dict):
        _note_parse_failure("_date_from_civil", value)
        return None
    date = value.get("date") if "date" in value else value
    if not isinstance(date, dict) or "year" not in date:
        _note_parse_failure("_date_from_civil", value)
        return None
    try:
        return f"{int(date['year']):04d}-{int(date['month']):02d}-{int(date['day']):02d}"
    except (KeyError, TypeError, ValueError):
        _note_parse_failure("_date_from_civil", value)
        return None


def _time_from_civil(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    time = value.get("time") if "time" in value else value
    if not isinstance(time, dict) or "hours" not in time:
        return None
    return f"{int(time['hours']):02d}:{int(time.get('minutes') or 0):02d}"


def _offset_seconds(value: Any) -> int | None:
    """v4 的 UTC 偏移是形如 "28800s" 的字符串。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return int(float(text))
    except ValueError:
        _note_parse_failure("_offset_seconds", value)
        return None


def _civil_iso(time_value: Any, offset_value: Any = None) -> str | None:
    """把（物理时间, UTC 偏移）转成带本地偏移的 ISO 字符串。

    v4 的 record 类接口（sleep / exercise / ecg）只给物理时间（`...Z`）与
    `"28800s"` 形式的偏移，没有 `civil*` 字段。直接沿用 UTC 字符串会导致：
    1) 按天归属错 8 小时（跨凌晨的睡眠/运动被算到前一日）；
    2) 界面时间显示偏 8 小时（`slice(11, 16)` 取到的是 UTC 小时）。
    """
    if not isinstance(time_value, str) or not time_value.strip():
        return None
    text = time_value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    seconds = _offset_seconds(offset_value)
    if seconds is None:
        return parsed.isoformat(timespec="seconds")
    try:
        local = parsed.astimezone(timezone(timedelta(seconds=seconds)))
    except (OverflowError, ValueError):
        return parsed.isoformat(timespec="seconds")
    return local.isoformat(timespec="seconds")


def _civil_date(time_value: Any, offset_value: Any = None) -> str | None:
    iso = _civil_iso(time_value, offset_value)
    return iso[:10] if iso else None


def _civil_time(time_value: Any, offset_value: Any = None) -> str | None:
    iso = _civil_iso(time_value, offset_value)
    return iso[11:16] if iso else None


def _duration_seconds(value: Any) -> float | None:
    """v4 的 duration 是形如 "1920s" 的字符串。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _float(str(value).strip().rstrip("s"))


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


# ---------- 响应容器 ----------


def _meta(payload: dict, key: str) -> dict:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _points(payload: dict, key: str) -> list[dict]:
    data_points = _meta(payload, key).get("dataPoints")
    return [p for p in data_points if isinstance(p, dict)] if isinstance(data_points, list) else []


def _rollup(payload: dict, key: str, extractor: Callable[[dict], Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    points = _meta(payload, key).get("rollupDataPoints")
    if not isinstance(points, list):
        return result
    for point in points:
        if not isinstance(point, dict):
            continue
        date = _date_from_civil(point.get("civilStartTime")) or _civil_date(
            point.get("startTime"), point.get("startUtcOffset")
        )
        if date:
            result[date] = extractor(point)
    return result


def _records(payload: dict, key: str, record_key: str, extractor: Callable[[dict], Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for point in _points(payload, key):
        record = point.get(record_key)
        if not isinstance(record, dict):
            continue
        date = _date_from_civil(record.get("date"))
        if date:
            result[date] = extractor(record)
    return result


# ---------- 睡眠 ----------

_STAGE_ALIASES = {
    "awake": "wake",
    "restless": "wake",
    "wake": "wake",
    "asleep": "light",
    "light": "light",
    "deep": "deep",
    "rem": "rem",
}

_DETAILED_STAGES = ("light", "deep", "rem")


def _stage_totals(stage_summaries: Any) -> dict[str, int]:
    """stagesSummary → {deep/light/rem/wake: 分钟}。

    两个已确认的 v4 怪癖：
    1. reconcile 响应会重复返回同一聚合 → 按原始阶段名取 max 去重；
    2. 存在明细阶段（LIGHT/DEEP/REM）时，ASLEEP 是它们的合计 → 必须跳过，否则分钟数双计。
    """
    dedup: dict[str, float] = {}
    if isinstance(stage_summaries, list):
        for stage in stage_summaries:
            if not isinstance(stage, dict):
                continue
            raw = str(stage.get("type") or "").lower()
            if not raw:
                continue
            minutes = _float(stage.get("minutes")) or 0.0
            previous = dedup.get(raw)
            dedup[raw] = minutes if previous is None else max(previous, minutes)
    has_detailed = any(name in dedup for name in _DETAILED_STAGES)
    totals: dict[str, int] = {}
    for raw, minutes in dedup.items():
        if raw == "asleep" and has_detailed:
            continue
        key = _STAGE_ALIASES.get(raw)
        if key is None:
            continue
        totals[key] = totals.get(key, 0) + int(round(minutes))
    return totals


def _sleep_session(sleep: dict) -> SleepSession:
    summary = sleep.get("summary") if isinstance(sleep.get("summary"), dict) else {}
    interval = sleep.get("interval") if isinstance(sleep.get("interval"), dict) else {}
    asleep = _float(summary.get("minutesAsleep"))
    in_bed = _float(summary.get("minutesInSleepPeriod"))
    efficiency = int(round(asleep / in_bed * 100)) if asleep is not None and in_bed else None
    timeline: list[StageSegment] = []
    segments = sleep.get("stages")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            stage = _STAGE_ALIASES.get(str(segment.get("type") or "").lower())
            start = _civil_iso(segment.get("startTime"), segment.get("startUtcOffset"))
            end = _civil_iso(segment.get("endTime"), segment.get("endUtcOffset"))
            if stage and start and end:
                timeline.append(StageSegment(start=start, end=end, stage=stage))
    metadata = sleep.get("metadata") if isinstance(sleep.get("metadata"), dict) else {}
    return SleepSession(
        # 真实 v4 载荷用 metadata.mainSleep 标记主睡眠；旧形状用 nap 标记小睡。
        is_main=metadata.get("mainSleep") is not False and metadata.get("nap") is not True,
        start_time=_civil_iso(interval.get("startTime"), interval.get("startUtcOffset")),
        end_time=_civil_iso(interval.get("endTime"), interval.get("endUtcOffset")),
        total_minutes=_int(asleep),
        time_in_bed_minutes=_int(in_bed),
        efficiency=efficiency,
        minutes_awake=_int(summary.get("minutesAwake")),
        minutes_to_fall_asleep=_int(summary.get("minutesToFallAsleep")),
        minutes_after_wake_up=_int(summary.get("minutesAfterWakeUp")),
        stages=_stage_totals(summary.get("stagesSummary")),
        stage_timeline=timeline,
    )


# ---------- 运动 ----------

_ZONE_KEYS = {
    "light": "lightTime",
    "moderate": "moderateTime",
    "vigorous": "vigorousTime",
    "peak": "peakTime",
}


def _workout(exercise: dict) -> Workout:
    summary = exercise.get("metricsSummary") if isinstance(exercise.get("metricsSummary"), dict) else {}
    interval = exercise.get("interval") if isinstance(exercise.get("interval"), dict) else {}
    duration_seconds = _duration_seconds(exercise.get("activeDuration"))
    if duration_seconds is None:
        duration_seconds = _seconds_between(interval.get("startTime"), interval.get("endTime"))
    zone_minutes = None
    raw_zones = summary.get("heartRateZoneDurations")
    if isinstance(raw_zones, dict) and raw_zones:
        zone_minutes = {}
        for name, key in _ZONE_KEYS.items():
            seconds = _duration_seconds(raw_zones.get(key))
            if seconds is not None:
                zone_minutes[name] = round(seconds / 60, 1)
    name = exercise.get("displayName")
    if not name:
        name = str(exercise.get("exerciseType") or t('server.providers.google_health.translate._workout.a9d130d79')).replace("_", " ").title()
    return Workout(
        name=str(name),
        start_time=_civil_iso(interval.get("startTime"), interval.get("startUtcOffset")),
        duration_minutes=round(duration_seconds / 60, 1) if duration_seconds is not None else None,
        calories=_num1(summary.get("caloriesKcal")),
        distance_km=_float(summary.get("distanceMillimeters"), transform=lambda mm: round(mm / 1_000_000, 2)),
        average_hr=_num1(summary.get("averageHeartRateBeatsPerMinute")),
        steps=_num1(summary.get("steps")),
        zone_minutes=zone_minutes,
    )


# ---------- 日内序列 ----------


def _dominant_points(points: list[dict]) -> list[dict]:
    """同一指标可能同时来自手表 / 手机 / HealthKit，全部累加会重复计数。

    实测案例：同一天的步数会同时出现在手表、手机和 HealthKit 三个来源里，
    三份数值量级相近，累加后约为真实步数的三倍。只保留优先级最高的单一来源：
    Fitbit 平台设备 > Fitbit 平台其它 > 无设备名（HealthKit 等）。
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for point in points:
        source = point.get("dataSource") if isinstance(point.get("dataSource"), dict) else {}
        device = source.get("device") if isinstance(source.get("device"), dict) else {}
        label = (
            str(source.get("platform") or ""),
            str(device.get("displayName") or ""),
            str(source.get("recordingMethod") or ""),
        )
        groups.setdefault(label, []).append(point)
    if not groups:
        return []

    def score(item: tuple[tuple[str, str, str], list[dict]]) -> tuple[int, int]:
        (platform, device, _), items = item
        rank = 4 if platform.upper() == "FITBIT" else 0
        if device and device.lower() != "mobiletrack":
            rank += 2
        if not device:
            rank -= 1
        return (rank, len(items))

    return max(groups.items(), key=score)[1]


def _steps_intraday(payload: dict, date: str) -> list[TimePoint]:
    """分时步数只保留归属选中民用日的点（窗口载荷含多天，必须过滤）。"""
    points: list[TimePoint] = []
    for point in _dominant_points(_points(payload, "stepsIntradayRaw")):
        record = point.get("steps") if isinstance(point.get("steps"), dict) else {}
        interval = record.get("interval") if isinstance(record.get("interval"), dict) else {}
        point_date = _date_from_civil(interval.get("civilStartTime")) or _civil_date(
            interval.get("startTime"), interval.get("startUtcOffset")
        )
        if point_date and point_date != date:
            continue
        time = _time_from_civil(interval.get("civilStartTime")) or _civil_time(
            interval.get("startTime"), interval.get("startUtcOffset")
        )
        count = _float(record.get("count"))
        if time and count is not None:
            points.append(TimePoint(time=time, value=count))
    points.sort(key=lambda item: item.time)
    return points


def _hr_intraday(payload: dict, date: str) -> list[TimePoint]:
    """分时心率只保留归属选中民用日的点（窗口载荷含多天，必须过滤）。"""
    points: list[TimePoint] = []
    for point in _dominant_points(_points(payload, "heartIntradayRaw")):
        record = point.get("heartRate") if isinstance(point.get("heartRate"), dict) else {}
        sample = record.get("sampleTime") if isinstance(record.get("sampleTime"), dict) else {}
        point_date = _date_from_civil(sample.get("civilTime")) or _civil_date(
            sample.get("physicalTime"), sample.get("utcOffset")
        )
        if point_date and point_date != date:
            continue
        time = _time_from_civil(sample.get("civilTime")) or _civil_time(
            sample.get("physicalTime"), sample.get("utcOffset")
        )
        beats = _float(record.get("beatsPerMinute"))
        if time and beats:
            points.append(TimePoint(time=time, value=beats))
    # 真实设备心率约 2–3 秒一个采样（~3.5 万点/天）。按分钟求均值入库：
    # 保留日内曲线细节，同时避免单日快照膨胀到 1MB+（290 天/年 ≈ 24MB）。
    buckets: dict[str, list[float]] = {}
    for point in points:
        buckets.setdefault(point.time, []).append(point.value)
    return [
        TimePoint(time=time, value=round(sum(values) / len(values), 1))
        for time, values in sorted(buckets.items())
    ]


# ---------- 主翻译 ----------


def translate_payload(raw: RawDay) -> DaySnapshot:
    """翻译入口：顺手收集「有值但解析失败」的字段，写到 raw.parse_issues。"""
    issues: list[dict] = []
    token = _PARSE_ISSUES.set(issues)
    try:
        snapshot = _translate_payload(raw)
    finally:
        _PARSE_ISSUES.reset(token)
        raw.parse_issues = issues
    return snapshot


def _translate_payload(raw: RawDay) -> DaySnapshot:
    payload = raw.payload
    date = raw.date

    # 元数据
    user_info = _meta(payload, "userInfo")
    profile_raw = _meta(payload, "profileRaw")
    settings = _meta(payload, "settingsRaw")
    profile = Profile(
        display_name=user_info.get("name"),
        avatar_url=user_info.get("picture"),
        member_since=_date_from_civil(profile_raw.get("membershipStartDate")),
        timezone=_first(settings.get("timeZone"), profile_raw.get("timeZone")),
    )

    device = None
    devices = _meta(payload, "devicesRaw").get("pairedDevices")
    if isinstance(devices, list) and devices and isinstance(devices[0], dict):
        raw_device = devices[0]
        raw_name = str(raw_device.get("name") or "")
        device = Device(
            id=raw_name.rsplit("/", 1)[-1] or None,
            name=_first(raw_device.get("deviceType"), raw_device.get("deviceName")),
            type=raw_device.get("deviceType"),
            battery=raw_device.get("batteryStatus"),
            battery_level=_int(raw_device.get("batteryLevel")),
            last_sync_time=raw_device.get("lastSyncTime"),
            features=[str(feature) for feature in raw_device.get("features") or []],
        )

    # 日汇总（dailyRollUp）
    steps_map = _rollup(payload, "stepsDaily", lambda p: _int((p.get("steps") or {}).get("countSum")))
    calories_map = _rollup(
        payload, "caloriesDaily", lambda p: _num1((p.get("totalCalories") or {}).get("kcalSum"))
    )
    distance_map = _rollup(
        payload,
        "distanceDaily",
        lambda p: _float(
            (p.get("distance") or {}).get("millimetersSum"), transform=lambda mm: round(mm / 1_000_000, 2)
        ),
    )
    floors_map = _rollup(payload, "floorsDaily", lambda p: _int((p.get("floors") or {}).get("countSum")))

    def _activity_levels(point: dict) -> dict[str, int] | None:
        block = point.get("activeMinutes") if isinstance(point.get("activeMinutes"), dict) else {}
        levels: dict[str, int] = {}
        for item in block.get("activeMinutesRollupByActivityLevel") or []:
            if isinstance(item, dict) and item.get("activityLevel"):
                value = _int(item.get("activeMinutesSum"))
                if value is not None:
                    levels[str(item["activityLevel"]).upper()] = value
        return levels or None

    active_minutes_map = _rollup(payload, "activeMinutesDaily", _activity_levels)

    def _zone_total(point: dict) -> int | None:
        block = point.get("activeZoneMinutes")
        if not isinstance(block, dict) or not block:
            return None
        total = 0.0
        found = False
        for value in block.values():
            number = _float(value)
            if number is not None:
                total += number
                found = True
        return int(round(total)) if found else None

    zone_map = _rollup(payload, "zoneMinutesDaily", _zone_total)

    def _sedentary_minutes(point: dict) -> float | None:
        seconds = _duration_seconds((point.get("sedentaryPeriod") or {}).get("durationSum"))
        return None if seconds is None else round(seconds / 60, 1)

    sedentary_map = _rollup(payload, "sedentaryDaily", _sedentary_minutes)
    weight_map = _rollup(
        payload,
        "weightDaily",
        lambda p: _float(
            (p.get("weight") or {}).get("weightGramsAvg"), transform=lambda grams: round(grams / 1000, 2)
        ),
    )
    fat_map = _rollup(payload, "fatDaily", lambda p: _num1((p.get("bodyFat") or {}).get("bodyFatPercentageAvg")))
    water_map = _rollup(
        payload,
        "waterDaily",
        lambda p: _int(((p.get("hydrationLog") or {}).get("amountConsumed") or {}).get("millilitersSum")),
    )
    nutrition_map = _rollup(
        payload,
        "nutritionDaily",
        lambda p: _num1(((p.get("nutritionLog") or {}).get("energy") or {}).get("kcalSum")),
    )
    core_temp_map = _rollup(
        payload,
        "coreTemperatureDaily",
        lambda p: _num1((p.get("coreBodyTemperature") or {}).get("temperatureCelsiusAvg")),
    )

    # 每日记录（reconcile / list）
    resting_map = _records(payload, "restingHeartRaw", "dailyRestingHeartRate", lambda r: _int(r.get("beatsPerMinute")))
    hrv_map = _records(
        payload,
        "hrvRaw",
        "dailyHeartRateVariability",
        lambda r: {
            "ms": _num1(
                _first(
                    r.get("averageHeartRateVariabilityMilliseconds"),
                    r.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"),
                )
            ),
            "deep": _num1(r.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds")),
            "entropy": _num2(r.get("entropy")),
            "non_rem": _num1(r.get("nonRemHeartRateBeatsPerMinute")),
        },
    )
    spo2_map = _records(
        payload,
        "spo2Raw",
        "dailyOxygenSaturation",
        lambda r: {
            "avg": _num1(r.get("averagePercentage")),
            "min": _num1(r.get("lowerBoundPercentage")),
            "max": _num1(r.get("upperBoundPercentage")),
        },
    )
    breathing_map = _records(payload, "breathingRaw", "dailyRespiratoryRate", lambda r: _num1(r.get("breathsPerMinute")))
    skin_temp_map = _records(
        payload,
        "skinTemperatureRaw",
        "dailySleepTemperatureDerivations",
        lambda r: {
            "nightly": _num2(r.get("nightlyTemperatureCelsius")),
            "baseline": _num2(r.get("baselineTemperatureCelsius")),
            "stddev": _num2(r.get("relativeNightlyStddev30dCelsius")),
        },
    )
    cardio_map = _records(payload, "cardioRaw", "dailyVo2Max", lambda r: _num1(r.get("vo2Max")))

    # 睡眠（按 civil 结束日归属；小睡单独统计）
    sessions: list[tuple[str | None, SleepSession]] = []
    for point in _points(payload, "sleepRaw"):
        sleep = point.get("sleep")
        if not isinstance(sleep, dict):
            continue
        interval = sleep.get("interval") if isinstance(sleep.get("interval"), dict) else {}
        session_date = (
            _date_from_civil(interval.get("civilEndTime"))
            or _civil_date(interval.get("endTime"), interval.get("endUtcOffset"))
            or str(interval.get("endTime") or "")[:10]
        )
        sessions.append((session_date or None, _sleep_session(sleep)))
    day_sessions = [session for session_date, session in sessions if session_date == date]
    main_sleep = next((session for session in day_sessions if session.is_main), None)
    if main_sleep is None and day_sessions:
        main_sleep = max(day_sessions, key=lambda session: session.total_minutes or 0)
    naps = [session for session in day_sessions if session is not main_sleep]
    naps_minutes = sum(session.total_minutes or 0 for session in naps) if naps else None
    naps_count = len(naps) if naps else None

    # 运动（只保留归属于选中日的场次）
    workouts: list[Workout] = []
    for point in _points(payload, "activitiesRaw"):
        exercise = point.get("exercise")
        if not isinstance(exercise, dict):
            continue
        interval = exercise.get("interval") if isinstance(exercise.get("interval"), dict) else {}
        start_date = _civil_date(interval.get("startTime"), interval.get("startUtcOffset")) or str(
            interval.get("startTime") or ""
        )[:10]
        if start_date != date:
            continue
        workouts.append(_workout(exercise))

    # 日内序列（窗口载荷含多天 → 仅保留选中日）
    steps_intraday = _steps_intraday(payload, date)
    hr_intraday = _hr_intraday(payload, date)
    hr_values = [point.value for point in hr_intraday if point.value]
    hr_min = min(hr_values) if hr_values else None
    hr_max = max(hr_values) if hr_values else None
    hr_avg = round(sum(hr_values) / len(hr_values), 1) if hr_values else None

    # 告警（None = 无数据；0 = 有数据、无告警）
    irn_present = "irnAlertsRaw" in payload
    irn_points = _points(payload, "irnAlertsRaw")
    ecg_readings: list[dict] = []
    for point in _points(payload, "ecgRaw"):
        record = point.get("electrocardiogram")
        if not isinstance(record, dict):
            continue
        interval = record.get("interval") if isinstance(record.get("interval"), dict) else {}
        reading: dict[str, Any] = {
            "start_time": _civil_iso(interval.get("startTime"), interval.get("startUtcOffset"))
        }
        classification = _first(record.get("resultClassification"), record.get("classification"))
        if classification:
            reading["classification"] = str(classification)
        ecg_readings.append(reading)
    alerts = HealthAlerts(
        irregular_rhythm_alerts=len(irn_points) if irn_present else None,
        ecg_readings=ecg_readings,
    )

    # 组装
    levels_today = active_minutes_map.get(date) or {}
    moderate = levels_today.get("MODERATE")
    vigorous = levels_today.get("VIGOROUS")
    active_minutes = (moderate or 0) + (vigorous or 0) if (moderate is not None or vigorous is not None) else None

    hrv_today = hrv_map.get(date) or {}
    spo2_today = spo2_map.get(date) or {}
    skin_today = skin_temp_map.get(date) or {}
    skin_delta = None
    if skin_today.get("nightly") is not None and skin_today.get("baseline") is not None:
        skin_delta = round(skin_today["nightly"] - skin_today["baseline"], 2)

    activity = ActivityDay(
        steps=steps_map.get(date),
        calories_out=calories_map.get(date),
        distance_km=distance_map.get(date),
        floors=floors_map.get(date),
        active_minutes=active_minutes,
        light_active_minutes=levels_today.get("LIGHT"),
        moderate_active_minutes=moderate,
        vigorous_active_minutes=vigorous,
        zone_minutes=zone_map.get(date),
        sedentary_minutes=sedentary_map.get(date),
        steps_intraday=steps_intraday,
    )
    heart = HeartDay(
        resting_hr=resting_map.get(date),
        hr_min=hr_min,
        hr_max=hr_max,
        hr_avg=hr_avg,
        hr_intraday=hr_intraday,
        hrv_ms=hrv_today.get("ms"),
        hrv_deep_rmssd_ms=hrv_today.get("deep"),
        hrv_entropy=hrv_today.get("entropy"),
        non_rem_hr=hrv_today.get("non_rem"),
        spo2=spo2_today.get("avg"),
        spo2_min=spo2_today.get("min"),
        spo2_max=spo2_today.get("max"),
        breathing_rate=breathing_map.get(date),
        skin_temp_delta_c=skin_delta,
        skin_temp_nightly_c=skin_today.get("nightly"),
        skin_temp_baseline_c=skin_today.get("baseline"),
        skin_temp_stddev_30d_c=skin_today.get("stddev"),
        core_temp_c=core_temp_map.get(date),
        vo2max=cardio_map.get(date),
    )
    body = BodyDay(
        weight_kg=weight_map.get(date),
        body_fat_pct=fat_map.get(date),
        water_ml=water_map.get(date),
        calories_in=nutrition_map.get(date),
    )
    coverage = Coverage(
        endpoint_count=int(raw.stats.get("total") or 0),
        success_count=int(raw.stats.get("succeeded") or 0),
        errors=list(raw.errors),
    )
    return DaySnapshot(
        date=date,
        source=raw.source,
        generated_at=raw.generated_at,
        profile=profile,
        device=device,
        activity=activity,
        heart=heart,
        sleep=main_sleep,
        naps_minutes=naps_minutes,
        naps_count=naps_count,
        body=body,
        workouts=workouts,
        alerts=alerts,
        coverage=coverage,
    )
