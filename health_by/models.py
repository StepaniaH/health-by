"""域模型：所有数据源必须产出的单一内部契约（DaySnapshot）。

原则：视图不依赖任何远程 API 形状；该契约字段一律可为 None（部分数据缺失是常态）。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

SLEEP_STAGES = ("deep", "light", "rem", "wake")

TREND_METRIC_KEYS = (
    "steps",
    "calories_out",
    "distance_km",
    "floors",
    "active_minutes",
    "zone_minutes",
    "sedentary_minutes",
    "resting_hr",
    "hrv_ms",
    "spo2",
    "breathing_rate",
    "skin_temp_delta",
    "core_temp",
    "vo2max",
    "weight_kg",
    "body_fat",
    "water_ml",
    "calories_in",
    "sleep_minutes",
    "sleep_efficiency",
)


@dataclass
class TimePoint:
    time: str  # "HH:MM"，设备本地民用时间
    value: float


@dataclass
class StageSegment:
    start: str
    end: str
    stage: str  # deep | light | rem | wake


@dataclass
class SleepSession:
    is_main: bool = True
    start_time: str | None = None
    end_time: str | None = None
    total_minutes: int | None = None  # 实睡时长
    time_in_bed_minutes: int | None = None
    efficiency: int | None = None  # 0-100
    minutes_awake: int | None = None
    minutes_to_fall_asleep: int | None = None
    minutes_after_wake_up: int | None = None
    stages: dict[str, int] = field(default_factory=dict)  # deep/light/rem/wake 分钟
    stage_timeline: list[StageSegment] = field(default_factory=list)


@dataclass
class Workout:
    name: str
    start_time: str | None = None
    duration_minutes: float | None = None
    calories: float | None = None
    distance_km: float | None = None
    average_hr: float | None = None
    steps: float | None = None
    zone_minutes: dict[str, float] | None = None  # light/moderate/vigorous/peak


@dataclass
class Device:
    id: str | None = None
    name: str | None = None
    type: str | None = None
    battery: str | None = None
    battery_level: int | None = None
    last_sync_time: str | None = None
    features: list[str] = field(default_factory=list)


@dataclass
class Profile:
    display_name: str | None = None
    avatar_url: str | None = None
    member_since: str | None = None
    timezone: str | None = None


@dataclass
class ActivityDay:
    steps: int | None = None
    calories_out: float | None = None
    distance_km: float | None = None
    floors: int | None = None
    active_minutes: int | None = None
    light_active_minutes: int | None = None
    moderate_active_minutes: int | None = None
    vigorous_active_minutes: int | None = None
    zone_minutes: int | None = None
    sedentary_minutes: float | None = None
    steps_intraday: list[TimePoint] = field(default_factory=list)


@dataclass
class HeartDay:
    resting_hr: int | None = None
    hr_min: float | None = None
    hr_max: float | None = None
    hr_avg: float | None = None
    hr_intraday: list[TimePoint] = field(default_factory=list)
    hrv_ms: float | None = None
    hrv_deep_rmssd_ms: float | None = None
    hrv_entropy: float | None = None
    non_rem_hr: float | None = None
    spo2: float | None = None
    spo2_min: float | None = None
    spo2_max: float | None = None
    breathing_rate: float | None = None
    skin_temp_delta_c: float | None = None
    skin_temp_nightly_c: float | None = None
    skin_temp_baseline_c: float | None = None
    skin_temp_stddev_30d_c: float | None = None
    core_temp_c: float | None = None
    vo2max: float | None = None


@dataclass
class BodyDay:
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    water_ml: int | None = None
    calories_in: float | None = None


@dataclass
class HealthAlerts:
    irregular_rhythm_alerts: int | None = None  # None = 无数据；0 = 有数据、无告警
    ecg_readings: list[dict] = field(default_factory=list)


@dataclass
class Coverage:
    endpoint_count: int = 0
    success_count: int = 0
    errors: list[dict] = field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        return self.success_count < self.endpoint_count


@dataclass
class DaySnapshot:
    date: str
    source: str
    generated_at: str
    profile: Profile | None = None
    device: Device | None = None
    activity: ActivityDay = field(default_factory=ActivityDay)
    heart: HeartDay = field(default_factory=HeartDay)
    sleep: SleepSession | None = None
    naps_minutes: int | None = None
    naps_count: int | None = None
    body: BodyDay = field(default_factory=BodyDay)
    workouts: list[Workout] = field(default_factory=list)
    alerts: HealthAlerts = field(default_factory=HealthAlerts)
    coverage: Coverage = field(default_factory=Coverage)

    def to_dict(self) -> dict:
        return asdict(self)


def metric_values(day: DaySnapshot) -> dict[str, float]:
    """从 DaySnapshot 提取可入库的指标行（None 跳过）。"""
    sleep = day.sleep
    raw: dict[str, float | int | None] = {
        "steps": day.activity.steps,
        "calories_out": day.activity.calories_out,
        "distance_km": day.activity.distance_km,
        "floors": day.activity.floors,
        "active_minutes": day.activity.active_minutes,
        "zone_minutes": day.activity.zone_minutes,
        "sedentary_minutes": day.activity.sedentary_minutes,
        "resting_hr": day.heart.resting_hr,
        "hrv_ms": day.heart.hrv_ms,
        "spo2": day.heart.spo2,
        "breathing_rate": day.heart.breathing_rate,
        "skin_temp_delta": day.heart.skin_temp_delta_c,
        "core_temp": day.heart.core_temp_c,
        "vo2max": day.heart.vo2max,
        "weight_kg": day.body.weight_kg,
        "body_fat": day.body.body_fat_pct,
        "water_ml": day.body.water_ml,
        "calories_in": day.body.calories_in,
        "sleep_minutes": sleep.total_minutes if sleep else None,
        "sleep_efficiency": sleep.efficiency if sleep else None,
    }
    return {
        key: float(value)
        for key, value in raw.items()
        if value is not None and math.isfinite(float(value))
    }
