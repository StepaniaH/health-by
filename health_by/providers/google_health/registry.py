"""读取任务注册表：31 个任务 → 原始载荷键（与翻译层一一对应）。"""

from __future__ import annotations

from health_by.i18n import t

from dataclasses import dataclass, field

TREND_DAYS = 14  # 趋势窗口
ECG_DAYS = 90  # ECG 单独使用更长的窗口


@dataclass(frozen=True)
class Job:
    key: str  # 载荷键（translate 消费）
    kind: str  # meta | rollup | records | intraday
    dtype: str = ""
    record: str = ""  # daily | sleep | ecg | sample | session | interval
    window: str = "trend"  # trend | ecg | day
    family: str = "all-sources"
    op: str = "reconcile"  # reconcile | list
    page_size: int | None = None
    path: str = ""
    params: dict = field(default_factory=dict)


META_JOBS = [
    Job("identity", "meta", path="/users/me/identity"),
    Job("profileRaw", "meta", path="/users/me/profile"),
    Job("settingsRaw", "meta", path="/users/me/settings"),
    Job("devicesRaw", "meta", path="/users/me/pairedDevices", params={"pageSize": "100"}),
    Job("userInfo", "meta", path="https://www.googleapis.com/oauth2/v3/userinfo"),
    Job("irnProfileRaw", "meta", path="/users/me/irnProfile"),
]

ROLLUP_JOBS = [
    Job(key, "rollup", dtype)
    for key, dtype in [
        ("stepsDaily", "steps"),
        ("caloriesDaily", "total-calories"),
        ("distanceDaily", "distance"),
        ("floorsDaily", "floors"),
        ("activeMinutesDaily", "active-minutes"),
        ("zoneMinutesDaily", "active-zone-minutes"),
        ("sedentaryDaily", "sedentary-period"),
        ("weightDaily", "weight"),
        ("fatDaily", "body-fat"),
        ("waterDaily", "hydration-log"),
        ("nutritionDaily", "nutrition-log"),
        ("coreTemperatureDaily", "core-body-temperature"),
    ]
]

RECORD_JOBS = [
    Job("restingHeartRaw", "records", "daily-resting-heart-rate", "daily"),
    Job("hrvRaw", "records", "daily-heart-rate-variability", "daily"),
    Job("spo2Raw", "records", "daily-oxygen-saturation", "daily"),
    Job("breathingRaw", "records", "daily-respiratory-rate", "daily"),
    Job("skinTemperatureRaw", "records", "daily-sleep-temperature-derivations", "daily"),
    Job("cardioRaw", "records", "daily-vo2-max", "daily"),
    Job("sleepRaw", "records", "sleep", "sleep", page_size=100),  # 历史睡眠可能由 HEALTH_KIT 桥接，不能限定 google-wearables
    Job("activitiesRaw", "records", "exercise", "session", page_size=25),
    Job("ecgRaw", "records", "electrocardiogram", "ecg", window="ecg", op="list"),
    Job("irnAlertsRaw", "records", "irregular-rhythm-notification", "session", op="list"),
    Job("glucoseRaw", "records", "blood-glucose", "sample"),
]

INTRADAY_JOBS = [
    Job("stepsIntradayRaw", "intraday", "steps", "interval", window="day", family="google-wearables"),
    Job("heartIntradayRaw", "intraday", "heart-rate", "sample", window="day", family="google-wearables"),
]

ALL_JOBS: list[Job] = [*META_JOBS, *ROLLUP_JOBS, *RECORD_JOBS, *INTRADAY_JOBS]

assert len(ALL_JOBS) == 31, t('server.providers.google_health.registry.labels.4edbb96ab', p1=len(ALL_JOBS))  # 与翻译层测试断言保持一致
