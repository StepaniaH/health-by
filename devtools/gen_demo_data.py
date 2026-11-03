"""演示数据源：生成 GHA v4 形状的原始载荷，走与真实数据相同的翻译管线。

按日期确定性（seed 由日期派生）：同一日期在任何窗口请求中值都相同——
跨请求一致、可复现，适合开发、测试与 UI 评审（演示优先原则）。
恢复指标（静息心率 / HRV）与当夜睡眠耦合、跑步日有次晨负荷——数据里
存在可发现的交叉关联，供关联分析板块真实演示。
"""

from __future__ import annotations

import random
from datetime import date as date_type, datetime, timedelta, timezone

from devtools.sample_payload import assemble_payload, civil_date, raw_day
from health_by.providers.base import Provider, RawDay
from health_by.providers.google_health.translate import translate_payload
from health_by.util import iso_range, iso_today, shift_iso

WINDOW_DAYS = 14  # 与真实数据源的趋势窗口保持一致

_HOUR_WEIGHTS = {
    7: 1.0,
    8: 1.8,
    9: 1.1,
    10: 0.7,
    11: 0.7,
    12: 1.3,
    13: 1.0,
    14: 0.7,
    15: 0.7,
    16: 0.9,
    17: 1.3,
    18: 1.9,
    19: 1.7,
    20: 1.2,
    21: 0.8,
    22: 0.5,
}


def _rng(*parts: str) -> random.Random:
    # 旧产品名是已发布的随机种子命名空间，改品牌不能改变同日演示数据。
    return random.Random("fitbit-by:" + ":".join(parts))


def _sleep_timeline(start_iso: str, total_minutes: int, rng: random.Random) -> list[tuple[str, str, str]]:
    """把在床时间切成 25~45 分钟的阶段片段（演示用；真实数据由 GHA stages 端点提供）。"""
    start = datetime.fromisoformat(start_iso)
    end = start + timedelta(minutes=total_minutes)
    pattern = ["light", "deep", "light", "rem", "light", "deep", "light", "wake"]
    if rng.random() < 0.5:
        pattern = ["light", "rem", "light", "deep", "light", "rem", "light", "wake"]
    segments: list[tuple[str, str, str]] = []
    cursor = start
    index = 0
    while cursor < end:
        stage = pattern[index % len(pattern)]
        chunk = rng.randint(25, 45)
        seg_end = min(cursor + timedelta(minutes=chunk), end)
        segments.append((stage, cursor.isoformat(timespec="minutes"), seg_end.isoformat(timespec="minutes")))
        cursor = seg_end
        index += 1
    return segments


def _sleep_spec(iso: str, is_weekend: bool, rng: random.Random) -> dict:
    start_h = rng.choice([22, 22, 23, 23])
    start_m = rng.randint(0, 59)
    asleep = rng.randint(400, 520) if is_weekend else rng.randint(360, 470)
    wake_gap = rng.randint(25, 55)
    in_bed = asleep + wake_gap
    end_total = start_h * 60 + start_m + in_bed
    end_h, end_m = (end_total // 60) % 24, end_total % 60
    deep = int(asleep * rng.uniform(0.16, 0.24))
    rem = int(asleep * rng.uniform(0.18, 0.26))
    light = asleep - deep - rem
    prev = shift_iso(iso, -1)
    spec = {
        "start": f"{prev}T{start_h:02d}:{start_m:02d}:00+08:00",
        "end": f"{iso}T{end_h:02d}:{end_m:02d}:00+08:00",
        "asleep": asleep,
        "in_bed": in_bed,
        "awake": wake_gap,
        "to_fall": rng.randint(3, 14),
        "after_wake": rng.randint(2, 10),
        "stages": {"deep": deep, "rem": rem, "light": light, "wake": wake_gap},
    }
    spec["timeline"] = _sleep_timeline(spec["start"], in_bed, rng)
    return spec


def _day_spec(iso: str) -> dict:
    rng = _rng(iso)
    weekday = date_type.fromisoformat(iso).weekday()
    is_weekend = weekday >= 5

    steps = rng.randint(4200, 9800) + (rng.randint(1500, 3200) if is_weekend else 0)
    moderate = rng.randint(18, 68)
    vigorous = rng.randint(4, 38)
    light = rng.randint(140, 260)

    ordinal = date_type.fromisoformat(iso).toordinal()
    weight = round(72.0 - (ordinal % 120) * 0.01 + rng.uniform(-0.25, 0.25), 2)

    # 前一晚的睡眠（跨夜到当天早晨）先行生成：恢复指标与之耦合，
    # 让演示数据具备可发现的交叉关联（睡得更长 → 静息心率更低、HRV 更高；
    # 前一晚是跑步日（周二/周五）时，次晨恢复略微受压）。
    sleep_spec: dict | None = None
    if rng.random() >= 0.06:  # 约 6% 的夜晚无数据——真实世界的不完整数据
        sleep_spec = _sleep_spec(iso, is_weekend, rng)
    asleep = sleep_spec["asleep"] if sleep_spec else None
    drift = (asleep - 440) / 100 if asleep is not None else 0.0
    prev_weekday = date_type.fromisoformat(shift_iso(iso, -1)).weekday()
    training_tired = rng.uniform(0.5, 2.0) if prev_weekday in (1, 4) else 0.0
    resting_hr = round(63.5 - drift * 3.4 + training_tired + rng.uniform(-1.6, 1.6))
    resting_hr = max(52, min(74, resting_hr))
    hrv = round(54 + drift * 10 - training_tired * 2.6 + rng.uniform(-5.5, 5.5), 1)
    hrv = max(28, min(96, hrv))

    spec: dict = {
        "date": iso,
        "steps": steps,
        "calories": round(1350 + steps * 0.095 + rng.uniform(-120, 160)),
        "distance_km": round(steps * rng.uniform(0.00068, 0.00082), 2),
        "floors": rng.randint(2, 24),
        "light_min": light,
        "moderate_min": moderate,
        "vigorous_min": vigorous,
        "zone_minutes": {
            "FAT_BURN": moderate + rng.randint(2, 12),
            "CARDIO": vigorous,
            "PEAK": rng.randint(0, 9),
        },
        "sedentary_min": round(rng.uniform(300, 470), 1),
        "weight_kg": weight,
        "body_fat": round(17.4 + (72.0 - weight) * 0.35 + rng.uniform(-0.4, 0.4), 1),
        "water_ml": rng.randint(1100, 2300),
        "calories_in": rng.randint(1600, 2350),
        "core_temp": round(rng.uniform(36.3, 36.9), 2),
        "resting_hr": resting_hr,
        "hrv_ms": hrv,
        "hrv_deep": round(hrv * rng.uniform(1.15, 1.4), 1),
        "hrv_entropy": round(rng.uniform(2.4, 4.1), 2),
        "non_rem_hr": round(resting_hr + rng.uniform(-3, 2), 1),
        "spo2": round(rng.uniform(96.0, 98.6), 1),
        "skin_nightly": round(36.1 + rng.uniform(-0.35, 0.35), 2),
        "skin_baseline": 36.2,
        "skin_stddev": round(rng.uniform(0.14, 0.32), 2),
        "vo2max": round(43 + rng.uniform(-1.5, 2.0), 1),
    }
    spec["spo2_low"] = round(spec["spo2"] - rng.uniform(2.4, 4.4), 1)
    spec["spo2_high"] = round(spec["spo2"] + rng.uniform(0.8, 1.8), 1)
    spec["breathing"] = round(rng.uniform(13.0, 15.8), 1)

    # 睡眠已在上方生成；这里补小睡（约 12% 的白天出现）
    if sleep_spec is not None:
        spec["sleep"] = sleep_spec
        if rng.random() < 0.12:  # 小睡
            nap_start = rng.randint(13, 15)
            nap_len = rng.randint(20, 45)
            nap_end = nap_start * 60 + 5 + nap_len
            spec["naps"] = [
                {
                    "start": f"{iso}T{nap_start:02d}:05:00+08:00",
                    "end": f"{iso}T{(nap_end // 60) % 24:02d}:{nap_end % 60:02d}:00+08:00",
                    "asleep": nap_len - 2,
                    "in_bed": nap_len,
                    "stages": {"light": nap_len - 2, "wake": 2},
                    "nap": True,
                }
            ]

    # 运动：周二/周五跑步、周六骑行、周日散步（各带随机波动）
    workouts = []
    if weekday in (1, 4):
        minutes = rng.randint(28, 48)
        speed = rng.uniform(10.2, 12.6)  # km/h
        workouts.append(
            {
                "name": 'Outdoor running',
                "start": f"{iso}T18:{rng.randint(2, 25):02d}:00+08:00",
                "active_duration": f"{minutes * 60}s",
                "calories": round(minutes * rng.uniform(8.2, 10.4)),
                "distance_km": round(minutes * speed / 60, 2),
                "avg_hr": rng.randint(142, 163),
                "steps": minutes * rng.randint(158, 176),
                "zone_minutes": {
                    "light": rng.randint(2, 6),
                    "moderate": rng.randint(9, 18),
                    "vigorous": rng.randint(11, 26),
                    "peak": rng.randint(0, 7),
                },
            }
        )
    elif weekday == 5 and rng.random() < 0.8:
        minutes = rng.randint(40, 75)
        workouts.append(
            {
                "name": 'Cycling',
                "start": f"{iso}T10:{rng.randint(5, 40):02d}:00+08:00",
                "active_duration": f"{minutes * 60}s",
                "calories": round(minutes * rng.uniform(6.0, 8.0)),
                "distance_km": round(minutes * rng.uniform(18.0, 24.0) / 60, 2),
                "avg_hr": rng.randint(128, 148),
                "zone_minutes": {
                    "light": rng.randint(4, 10),
                    "moderate": rng.randint(12, 24),
                    "vigorous": rng.randint(6, 18),
                    "peak": rng.randint(0, 3),
                },
            }
        )
    elif weekday == 6 and rng.random() < 0.6:
        minutes = rng.randint(25, 50)
        workouts.append(
            {
                "name": 'Walking',
                "start": f"{iso}T16:{rng.randint(0, 40):02d}:00+08:00",
                "active_duration": f"{minutes * 60}s",
                "calories": round(minutes * rng.uniform(3.4, 4.6)),
                "distance_km": round(minutes * rng.uniform(4.2, 5.4) / 60, 2),
                "avg_hr": rng.randint(96, 112),
                "zone_minutes": {"light": minutes, "moderate": rng.randint(0, 6), "vigorous": 0, "peak": 0},
            }
        )
    if workouts:
        spec["workouts"] = workouts

    # 日内步数：按小时权重分配
    total_weight = sum(_HOUR_WEIGHTS.values())
    intraday = []
    for hour, weight in sorted(_HOUR_WEIGHTS.items()):
        share = int(round(steps * weight / total_weight))
        if share > 0:
            intraday.append((f"{hour:02d}:{rng.randint(0, 45):02d}", share))
    spec["steps_intraday"] = intraday

    # 日内心率：07:00-22:30 每 30 分钟；运动时段抬高
    workout_hour = str(workouts[0]["start"])[11:13] if workouts else None
    hr_points = []
    for hour in range(7, 23):
        for minute in (0, 30):
            hhmm = f"{hour:02d}:{minute:02d}"
            base = resting_hr + rng.randint(4, 16)
            if workout_hour == f"{hour:02d}":
                base += rng.randint(30, 55)
            hr_points.append((hhmm, base))
    spec["hr_intraday"] = hr_points

    return spec


def _device_sync_time(date: str) -> str:
    """演示设备的"最近同步时间"：过去日期固定在早上 06:40Z；今天用 ~2 小时前，
    否则每天中午后演示看板都会显示"数据过期"（那是真实世界的语义，不是演示效果）。"""
    if date < iso_today():
        return f"{date}T06:40:00Z"
    stamp = datetime.now(timezone.utc) - timedelta(hours=2)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


class DemoProvider(Provider):
    name = "demo"
    display_name = 'Synthetic data'
    auth_kind = "none"

    def auth_status(self, credentials: dict | None) -> dict:
        return {"configured": True, "connected": False, "needs_reconnect": False}

    def fetch_raw(
        self, date: str, *, credentials: dict | None = None, http=None, on_progress=None
    ) -> RawDay:
        days = [_day_spec(iso) for iso in iso_range(date, WINDOW_DAYS)]
        payload = assemble_payload(
            days,
            profile={
                "display_name": 'Synthetic user',
                "timeZone": "Asia/Shanghai",
                "membershipStartDate": civil_date("2025-03-01")["date"],
            },
            device={
                "name": "users/me/pairedDevices/demo-tracker",
                "deviceType": "FITBIT_AIR",
                "batteryStatus": "Good",
                "batteryLevel": 78,
                "lastSyncTime": _device_sync_time(date),
                "features": ["HEART_RATE", "SPO2", "SLEEP_STAGES"],
            },
        )
        return raw_day(date, payload, source="demo")

    def translate(self, raw: RawDay):
        return translate_payload(raw)
