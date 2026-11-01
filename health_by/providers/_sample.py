"""构造 Google Health v4 形状载荷的共享原语。

演示数据生成器与测试共用，保证"演示数据走真实翻译管线"的约定。
所有构造出的形状与 v4 官方响应结构对应（字段名来自调研文档与官方参考）。
"""

from __future__ import annotations

from typing import Any

from .base import RawDay


# ---------- 基础形状 ----------


def civil_date(date: str) -> dict:
    year, month, day = (int(part) for part in date.split("-"))
    return {"date": {"year": year, "month": month, "day": day}}


def civil(date: str, hh: int = 0, mm: int = 0, ss: int = 0) -> dict:
    out = civil_date(date)
    out["time"] = {"hours": hh, "minutes": mm, "seconds": ss, "nanos": 0}
    return out


def rollup_point(date: str, field: str, inner: dict) -> dict:
    return {"civilStartTime": civil(date), field: inner}


def record_point(date: str, record_key: str, inner: dict) -> dict:
    return {record_key: {"date": civil_date(date)["date"], **inner}}


def _iso_time_parts(iso: str) -> tuple[int, int]:
    # "2026-10-15T07:02:00+08:00" -> (7, 2)
    return int(iso[11:13]), int(iso[14:16])


def sleep_point(spec: dict) -> dict:
    """spec: start/end (ISO), asleep, in_bed, awake, to_fall, after_wake,
    stages {light,deep,rem,wake}, timeline [(stage, start, end)], nap(bool)。"""
    end_iso = spec["end"]
    hh, mm = _iso_time_parts(end_iso)
    summary: dict[str, Any] = {
        "minutesAsleep": spec.get("asleep"),
        "minutesInSleepPeriod": spec.get("in_bed"),
        "minutesAwake": spec.get("awake"),
        "minutesToFallAsleep": spec.get("to_fall"),
        "minutesAfterWakeUp": spec.get("after_wake"),
    }
    stages_summary = [
        {"type": stage.upper(), "minutes": minutes, "count": 1}
        for stage, minutes in (spec.get("stages") or {}).items()
        if minutes is not None
    ]
    if spec.get("stages_summary") is not None:
        stages_summary = spec["stages_summary"]  # 允许显式覆盖（测试去重/双计场景）
    sleep = {
        "interval": {
            "startTime": spec["start"],
            "endTime": end_iso,
            "civilEndTime": civil(end_iso[:10], hh, mm),
        },
        "summary": {k: v for k, v in summary.items() if v is not None} | {"stagesSummary": stages_summary},
        "stages": [
            {"type": stage.upper(), "startTime": start, "endTime": end}
            for stage, start, end in (spec.get("timeline") or [])
        ],
        "metadata": {"nap": bool(spec.get("nap"))},
    }
    return {"sleep": sleep}


def exercise_point(spec: dict) -> dict:
    metrics: dict[str, Any] = {
        "caloriesKcal": spec.get("calories"),
        "distanceMillimeters": _to_mm(spec.get("distance_km")),
        "averageHeartRateBeatsPerMinute": spec.get("avg_hr"),
        "steps": spec.get("steps"),
        "averagePaceSecondsPerMeter": spec.get("pace_s_per_m"),
    }
    zones = spec.get("zone_minutes")
    if zones:
        metrics["heartRateZoneDurations"] = {
            "lightTime": f"{int(zones.get('light', 0) * 60)}s",
            "moderateTime": f"{int(zones.get('moderate', 0) * 60)}s",
            "vigorousTime": f"{int(zones.get('vigorous', 0) * 60)}s",
            "peakTime": f"{int(zones.get('peak', 0) * 60)}s",
        }
    if spec.get("active_zone_minutes") is not None:
        metrics["activeZoneMinutes"] = spec["active_zone_minutes"]
    exercise = {
        "displayName": spec.get("name"),
        "exerciseType": spec.get("type", "RUNNING"),
        "interval": {"startTime": spec["start"], "endTime": spec.get("end")},
        "activeDuration": spec.get("active_duration"),
        "metricsSummary": {k: v for k, v in metrics.items() if v is not None},
    }
    return {"exercise": exercise}


def _to_mm(distance_km: float | None) -> int | None:
    return None if distance_km is None else int(round(distance_km * 1_000_000))


def _to_grams(weight_kg: float | None) -> int | None:
    return None if weight_kg is None else int(round(weight_kg * 1000))


# ---------- 载荷组装 ----------


def assemble_payload(
    days: list[dict],
    *,
    profile: dict | None = None,
    device: dict | None = None,
    include: set[str] | None = None,
) -> dict:
    """把按天规格列表组装成完整的 v4 形状载荷。

    每天规格（均可缺省）：date, steps, calories, distance_km, floors,
    light_min/moderate_min/vigorous_min, zone_minutes{...}, sedentary_min,
    weight_kg, body_fat, water_ml, calories_in, core_temp, resting_hr, hrv_ms,
    hrv_deep, hrv_entropy, non_rem_hr, spo2, spo2_low, spo2_high, breathing,
    skin_nightly, skin_baseline, skin_stddev, vo2max, sleep{...}, naps[{...}],
    workouts[{...}], steps_intraday[(HH:MM, n)], hr_intraday[(HH:MM, bpm)]。

    include：可选集合，仅保留这些顶层键（用于构造"部分失败"场景）。
    """
    payload: dict[str, Any] = {}

    # 元数据
    payload["identity"] = {"name": "users/me"}
    payload["profileRaw"] = profile or {"timeZone": "Asia/Shanghai"}
    payload["settingsRaw"] = {"timeZone": "Asia/Shanghai"}
    payload["devicesRaw"] = {"pairedDevices": [device] if device else []}
    payload["userInfo"] = {"name": (profile or {}).get("display_name"), "picture": None}
    payload["irnProfileRaw"] = {}

    # 日汇总（dailyRollUp）
    def _levels(day: dict) -> list[dict] | None:
        levels = []
        for name, key in (("LIGHT", "light_min"), ("MODERATE", "moderate_min"), ("VIGOROUS", "vigorous_min")):
            if day.get(key) is not None:
                levels.append({"activityLevel": name, "activeMinutesSum": day[key]})
        return levels or None

    rollup_specs = [
        ("stepsDaily", "steps", lambda d: {"countSum": d.get("steps")}),
        ("caloriesDaily", "totalCalories", lambda d: {"kcalSum": d.get("calories")}),
        ("distanceDaily", "distance", lambda d: {"millimetersSum": _to_mm(d.get("distance_km"))}),
        ("floorsDaily", "floors", lambda d: {"countSum": d.get("floors")}),
        ("activeMinutesDaily", "activeMinutes", lambda d: {"activeMinutesRollupByActivityLevel": _levels(d)}),
        ("zoneMinutesDaily", "activeZoneMinutes", lambda d: d.get("zone_minutes")),
        ("sedentaryDaily", "sedentaryPeriod", lambda d: (
            {"durationSum": f"{int(round(float(d['sedentary_min']) * 60))}s"} if d.get("sedentary_min") is not None else {})),
        ("weightDaily", "weight", lambda d: {"weightGramsAvg": _to_grams(d.get("weight_kg"))}),
        ("fatDaily", "bodyFat", lambda d: {"bodyFatPercentageAvg": d.get("body_fat")}),
        ("waterDaily", "hydrationLog", lambda d: (
            {"amountConsumed": {"millilitersSum": d["water_ml"]}} if d.get("water_ml") is not None else {})),
        ("nutritionDaily", "nutritionLog", lambda d: (
            {"energy": {"kcalSum": d["calories_in"]}} if d.get("calories_in") is not None else {})),
        ("coreTemperatureDaily", "coreBodyTemperature", lambda d: {"temperatureCelsiusAvg": d.get("core_temp")}),
    ]
    for key, field, build in rollup_specs:
        points = []
        for day in days:
            inner = {k: v for k, v in (build(day) or {}).items() if v is not None}
            if inner:
                points.append(rollup_point(day["date"], field, inner))
        payload[key] = {"rollupDataPoints": points}

    # 每日记录（reconcile / list）
    record_specs = [
        ("restingHeartRaw", "dailyRestingHeartRate", lambda d: {"beatsPerMinute": d.get("resting_hr")}),
        ("hrvRaw", "dailyHeartRateVariability", lambda d: {
            "averageHeartRateVariabilityMilliseconds": d.get("hrv_ms"),
            "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": d.get("hrv_deep"),
            "entropy": d.get("hrv_entropy"),
            "nonRemHeartRateBeatsPerMinute": d.get("non_rem_hr"),
        }),
        ("spo2Raw", "dailyOxygenSaturation", lambda d: {
            "averagePercentage": d.get("spo2"),
            "lowerBoundPercentage": d.get("spo2_low"),
            "upperBoundPercentage": d.get("spo2_high"),
        }),
        ("breathingRaw", "dailyRespiratoryRate", lambda d: {"breathsPerMinute": d.get("breathing")}),
        ("skinTemperatureRaw", "dailySleepTemperatureDerivations", lambda d: {
            "nightlyTemperatureCelsius": d.get("skin_nightly"),
            "baselineTemperatureCelsius": d.get("skin_baseline"),
            "relativeNightlyStddev30dCelsius": d.get("skin_stddev"),
        }),
        ("cardioRaw", "dailyVo2Max", lambda d: {"vo2Max": d.get("vo2max")}),
    ]
    for key, record_key, build in record_specs:
        points = []
        for day in days:
            inner = {k: v for k, v in (build(day) or {}).items() if v is not None}
            if inner:
                points.append(record_point(day["date"], record_key, inner))
        payload[key] = {"dataPoints": points}

    # 睡眠（含小睡）
    sleep_points = []
    for day in days:
        for spec in ([day["sleep"]] if day.get("sleep") else []) + list(day.get("naps") or []):
            sleep_points.append(sleep_point(spec))
    payload["sleepRaw"] = {"dataPoints": sleep_points}

    # 运动
    activities = []
    for day in days:
        for spec in day.get("workouts") or []:
            activities.append(exercise_point(spec))
    payload["activitiesRaw"] = {"dataPoints": activities}

    # 日内序列
    step_points = []
    for day in days:
        for hhmm, count in day.get("steps_intraday") or []:
            hh, mm = int(hhmm[:2]), int(hhmm[3:5])
            step_points.append({
                "steps": {
                    "interval": {
                        "civilStartTime": civil(day["date"], hh, mm),
                        "startTime": f"{day['date']}T{hhmm}:00+08:00",
                    },
                    "count": count,
                }
            })
    payload["stepsIntradayRaw"] = {"dataPoints": step_points}

    hr_points = []
    for day in days:
        for hhmm, bpm in day.get("hr_intraday") or []:
            hh, mm = int(hhmm[:2]), int(hhmm[3:5])
            hr_points.append({
                "heartRate": {
                    "sampleTime": {
                        "civilTime": civil(day["date"], hh, mm),
                        "physicalTime": f"{day['date']}T{hhmm}:00+08:00",
                    },
                    "beatsPerMinute": bpm,
                }
            })
    payload["heartIntradayRaw"] = {"dataPoints": hr_points}

    # 低频/空默认
    payload["ecgRaw"] = {"dataPoints": []}
    payload["irnAlertsRaw"] = {"dataPoints": []}
    payload["glucoseRaw"] = {"dataPoints": []}

    if include is not None:
        payload = {key: value for key, value in payload.items() if key in include}
    return payload


def raw_day(date: str, payload: dict, *, source: str = "test", errors: list[dict] | None = None) -> RawDay:
    """约定：errors 里的 key 在 payload 中缺失（与真实抓取一致——失败的任务不产出键）。"""
    errors = errors or []
    return RawDay(
        date=date,
        source=source,
        generated_at="2026-10-15T00:00:00Z",
        payload=payload,
        errors=errors,
        stats={"total": len(payload) + len(errors), "succeeded": len(payload)},
    )
