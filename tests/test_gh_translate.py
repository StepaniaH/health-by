"""Google Health v4 翻译层测试（覆盖调研确认的 API 怪癖）。"""

from __future__ import annotations

from devtools.sample_payload import assemble_payload, civil, raw_day
from health_by.providers.base import RawDay
from health_by.providers.google_health.translate import translate_payload

DAY = "2026-10-15"
PREV = "2026-10-14"
REAL_DAY = "2026-10-27"  # 真实载荷回归用例：物理时间（Z）+ "28800s" 偏移，无 civil*


def base_payload(**overrides):
    day = {
        "date": DAY,
        "steps": 8123,
        "calories": 2100,
        "distance_km": 6.2,
        "floors": 12,
        "light_min": 210,
        "moderate_min": 45,
        "vigorous_min": 18,
        "zone_minutes": {"FAT_BURN": 30, "CARDIO": 12, "PEAK": 3},
        "sedentary_min": 330,
        "weight_kg": 72.5,
        "body_fat": 18.2,
        "water_ml": 1500,
        "calories_in": 1980,
        "core_temp": 36.6,
        "resting_hr": 61,
        "hrv_ms": 56.4,
        "hrv_deep": 71.2,
        "hrv_entropy": 3.1,
        "non_rem_hr": 58.5,
        "spo2": 97.2,
        "spo2_low": 93.8,
        "spo2_high": 99.1,
        "breathing": 14.3,
        "skin_nightly": 36.15,
        "skin_baseline": 36.32,
        "skin_stddev": 0.21,
        "vo2max": 44.8,
        "sleep": {
            "start": f"{PREV}T23:18:00+08:00",
            "end": f"{DAY}T07:02:00+08:00",
            "asleep": 402,
            "in_bed": 464,
            "awake": 52,
            "to_fall": 6,
            "after_wake": 4,
            "stages": {"light": 231, "deep": 78, "rem": 93, "wake": 52},
        },
        "workouts": [
            {
                "name": "户外跑步",
                "start": f"{DAY}T18:02:00+08:00",
                "end": f"{DAY}T18:36:00+08:00",
                "active_duration": "1920s",
                "calories": 323,
                "distance_km": 5.31,
                "avg_hr": 152,
                "steps": 5360,
                "zone_minutes": {"light": 4, "moderate": 12, "vigorous": 14, "peak": 2},
                "active_zone_minutes": 72,
            }
        ],
        "steps_intraday": [("08:15", 420), ("19:30", 350)],
        "hr_intraday": [("08:20", 96), ("19:35", 88)],
    }
    day.update(overrides)
    days = [{"date": PREV, "steps": 5000, "resting_hr": 63}, day]
    return assemble_payload(
        days,
        profile={"display_name": "测试用户", "timeZone": "Asia/Shanghai"},
        device={
            "name": "users/me/pairedDevices/abc",
            "deviceType": "FITBIT_AIR",
            "batteryStatus": "Good",
            "batteryLevel": 82,
            "lastSyncTime": f"{DAY}T06:30:00Z",
            "features": ["HEART_RATE"],
        },
    )


def test_full_day_translation():
    snapshot = translate_payload(raw_day(DAY, base_payload()))

    assert snapshot.date == DAY
    # 活动
    assert snapshot.activity.steps == 8123
    assert snapshot.activity.calories_out == 2100.0
    assert snapshot.activity.distance_km == 6.2
    assert snapshot.activity.floors == 12
    assert snapshot.activity.light_active_minutes == 210
    assert snapshot.activity.moderate_active_minutes == 45
    assert snapshot.activity.vigorous_active_minutes == 18
    assert snapshot.activity.active_minutes == 63  # moderate + vigorous
    assert snapshot.activity.zone_minutes == 45  # 30 + 12 + 3
    assert snapshot.activity.sedentary_minutes == 330.0
    assert [p.time for p in snapshot.activity.steps_intraday] == ["08:15", "19:30"]
    # 心率
    assert snapshot.heart.resting_hr == 61
    assert snapshot.heart.hr_min == 88
    assert snapshot.heart.hr_max == 96
    assert snapshot.heart.hr_avg == 92.0
    assert snapshot.heart.hrv_ms == 56.4
    assert snapshot.heart.hrv_deep_rmssd_ms == 71.2
    assert snapshot.heart.spo2 == 97.2
    assert snapshot.heart.spo2_min == 93.8
    assert snapshot.heart.breathing_rate == 14.3
    assert snapshot.heart.skin_temp_delta_c == -0.17
    assert snapshot.heart.core_temp_c == 36.6
    assert snapshot.heart.vo2max == 44.8
    # 睡眠
    sleep = snapshot.sleep
    assert sleep is not None and sleep.is_main
    assert sleep.total_minutes == 402
    assert sleep.time_in_bed_minutes == 464
    assert sleep.efficiency == 87  # round(402/464*100)
    assert sleep.stages == {"light": 231, "deep": 78, "rem": 93, "wake": 52}
    assert snapshot.naps_count is None and snapshot.naps_minutes is None
    # 身体
    assert snapshot.body.weight_kg == 72.5
    assert snapshot.body.body_fat_pct == 18.2
    assert snapshot.body.water_ml == 1500
    assert snapshot.body.calories_in == 1980.0
    # 运动
    assert len(snapshot.workouts) == 1
    workout = snapshot.workouts[0]
    assert workout.name == "户外跑步"
    assert workout.duration_minutes == 32.0
    assert workout.calories == 323.0
    assert workout.distance_km == 5.31
    assert workout.average_hr == 152.0
    assert workout.zone_minutes == {"light": 4.0, "moderate": 12.0, "vigorous": 14.0, "peak": 2.0}
    # 元数据 / 覆盖
    assert snapshot.profile.display_name == "测试用户"
    assert snapshot.profile.timezone == "Asia/Shanghai"
    assert snapshot.device.battery_level == 82
    assert snapshot.device.id == "abc"
    assert snapshot.coverage.endpoint_count == 31
    assert snapshot.coverage.success_count == 31
    assert not snapshot.coverage.is_partial


def test_rollup_uses_selected_date():
    snapshot = translate_payload(raw_day(DAY, base_payload()))
    assert snapshot.activity.steps == 8123  # 而不是 PREV 的 5000


def test_partial_payload_tolerated():
    payload = base_payload()
    failed = ("sleepRaw", "activitiesRaw", "stepsIntradayRaw", "heartIntradayRaw", "restingHeartRaw")
    for key in failed:
        payload.pop(key)
    errors = [{"key": key, "message": "no data", "status": 404} for key in failed]
    snapshot = translate_payload(raw_day(DAY, payload, errors=errors))
    assert snapshot.sleep is None
    assert snapshot.workouts == []
    assert snapshot.activity.steps_intraday == []
    assert snapshot.heart.resting_hr is None
    assert snapshot.heart.hr_min is None
    assert snapshot.coverage.endpoint_count == 31  # 26 成功 + 5 失败
    assert snapshot.coverage.success_count == 26
    assert snapshot.coverage.is_partial


def test_included_keys_only_partial():
    payload = assemble_payload([{"date": DAY, "steps": 4321}], include={"stepsDaily", "userInfo"})
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.activity.steps == 4321
    assert snapshot.coverage.endpoint_count == 2
    assert snapshot.coverage.success_count == 2
    assert snapshot.sleep is None
    assert snapshot.alerts.irregular_rhythm_alerts is None


def test_empty_payload_no_crash():
    snapshot = translate_payload(raw_day(DAY, {}))
    assert snapshot.activity.steps is None
    assert snapshot.sleep is None
    assert snapshot.coverage.endpoint_count == 0
    assert snapshot.alerts.irregular_rhythm_alerts is None


def test_asleep_aggregate_skipped_when_detailed():
    payload = base_payload(
        sleep={
            "start": f"{PREV}T23:00:00+08:00",
            "end": f"{DAY}T07:00:00+08:00",
            "asleep": 340,
            "in_bed": 420,
            "stages": {},
            "stages_summary": [
                {"type": "LIGHT", "minutes": 200, "count": 1},
                {"type": "DEEP", "minutes": 60, "count": 1},
                {"type": "REM", "minutes": 80, "count": 1},
                {"type": "ASLEEP", "minutes": 340, "count": 1},
                {"type": "AWAKE", "minutes": 60, "count": 1},
            ],
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.sleep.stages == {"light": 200, "deep": 60, "rem": 80, "wake": 60}


def test_asleep_aggregate_used_when_no_detailed():
    payload = base_payload(
        sleep={
            "start": f"{PREV}T23:30:00+08:00",
            "end": f"{DAY}T06:30:00+08:00",
            "asleep": 340,
            "in_bed": 400,
            "stages": {},
            "stages_summary": [
                {"type": "ASLEEP", "minutes": 340, "count": 1},
                {"type": "AWAKE", "minutes": 40, "count": 1},
            ],
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.sleep.stages == {"light": 340, "wake": 40}


def test_stage_duplicates_deduped_by_max():
    payload = base_payload(
        sleep={
            "start": f"{PREV}T23:00:00+08:00",
            "end": f"{DAY}T07:00:00+08:00",
            "asleep": 402,
            "in_bed": 464,
            "stages": {},
            "stages_summary": [
                {"type": "LIGHT", "minutes": 231, "count": 3},
                {"type": "LIGHT", "minutes": 300, "count": 5},
                {"type": "DEEP", "minutes": 78, "count": 2},
                {"type": "REM", "minutes": 93, "count": 2},
                {"type": "AWAKE", "minutes": 52, "count": 4},
            ],
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.sleep.stages["light"] == 300
    assert snapshot.sleep.stages["wake"] == 52


def test_nap_counted_separately():
    payload = base_payload()
    payload["sleepRaw"]["dataPoints"].append(
        {
            "sleep": {
                "interval": {
                    "startTime": f"{DAY}T14:02:00+08:00",
                    "endTime": f"{DAY}T14:27:00+08:00",
                    "civilEndTime": {"date": {"year": 2026, "month": 10, "day": 15}},
                },
                "summary": {"minutesAsleep": 24, "minutesInSleepPeriod": 25, "stagesSummary": []},
                "stages": [],
                "metadata": {"nap": True},
            }
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.sleep.total_minutes == 402  # 主睡眠不受影响
    assert snapshot.naps_count == 1
    assert snapshot.naps_minutes == 24


def test_efficiency_none_without_in_bed():
    payload = base_payload(
        sleep={
            "start": f"{PREV}T23:00:00+08:00",
            "end": f"{DAY}T06:00:00+08:00",
            "asleep": 380,
            "stages": {},
            "stages_summary": [],
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.sleep.efficiency is None
    assert snapshot.sleep.total_minutes == 380


def test_workout_duration_fallback_from_interval():
    payload = base_payload(
        workouts=[
            {
                "name": None,
                "type": "BIKING",
                "start": f"{DAY}T09:00:00+08:00",
                "end": f"{DAY}T09:34:00+08:00",
            }
        ]
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    workout = snapshot.workouts[0]
    assert workout.duration_minutes == 34.0
    assert workout.name == "Biking"  # 无 displayName 时用类型兜底


def test_workout_other_day_filtered_out():
    payload = base_payload(
        workouts=[
            {"name": "昨天跑步", "start": f"{PREV}T18:00:00+08:00", "end": f"{PREV}T18:30:00+08:00"},
            {"name": "今天跑步", "start": f"{DAY}T18:00:00+08:00", "end": f"{DAY}T18:30:00+08:00"},
        ]
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert [w.name for w in snapshot.workouts] == ["今天跑步"]


def test_hr_zero_samples_ignored():
    payload = base_payload()
    payload["heartIntradayRaw"]["dataPoints"].append(
        {
            "heartRate": {
                "sampleTime": {
                    "civilTime": {
                        "date": {"year": 2026, "month": 10, "day": 15},
                        "time": {"hours": 0, "minutes": 5},
                    }
                },
                "beatsPerMinute": 0,
            }
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.heart.hr_min == 88


def test_sleep_timeline_mapped():
    payload = base_payload(
        sleep={
            "start": f"{PREV}T23:00:00+08:00",
            "end": f"{DAY}T07:00:00+08:00",
            "asleep": 400,
            "in_bed": 460,
            "stages": {},
            "stages_summary": [],
            "timeline": [
                ("light", f"{PREV}T23:00:00+08:00", f"{PREV}T23:40:00+08:00"),
                ("deep", f"{PREV}T23:40:00+08:00", f"{DAY}T00:30:00+08:00"),
                ("unknown", f"{DAY}T00:30:00+08:00", f"{DAY}T00:40:00+08:00"),
            ],
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert [(s.stage, s.start) for s in snapshot.sleep.stage_timeline] == [
        ("light", f"{PREV}T23:00:00+08:00"),
        ("deep", f"{PREV}T23:40:00+08:00"),
    ]


def test_irregular_rhythm_alerts_counted():
    payload = base_payload()
    payload["irnAlertsRaw"] = {"dataPoints": [{"a": 1}, {"b": 2}]}
    snapshot = translate_payload(raw_day(DAY, payload))
    assert snapshot.alerts.irregular_rhythm_alerts == 2


def test_errors_and_stats_flow_into_coverage():
    payload = base_payload()
    payload.pop("ecgRaw")  # 失败的任务不产出键
    errors = [{"key": "ecgRaw", "message": "403", "status": 403}]
    snapshot = translate_payload(raw_day(DAY, payload, errors=errors))
    assert snapshot.coverage.errors == errors
    assert snapshot.coverage.endpoint_count == 31
    assert snapshot.coverage.success_count == 30


def test_intraday_series_filtered_to_target_day():
    """回归：窗口里其它天的分时点不得混入当日快照（曾导致时间轴「高峰」超过全天总数）。"""
    payload = base_payload()
    payload["stepsIntradayRaw"]["dataPoints"].append(
        {
            "steps": {
                "interval": {
                    "civilStartTime": civil(PREV, 23, 45),
                    "startTime": f"{PREV}T23:45:00+08:00",
                },
                "count": 500,
            }
        }
    )
    payload["heartIntradayRaw"]["dataPoints"].append(
        {
            "heartRate": {
                "sampleTime": {
                    "civilTime": civil(PREV, 23, 50),
                    "physicalTime": f"{PREV}T23:50:00+08:00",
                },
                "beatsPerMinute": 120,
            }
        }
    )
    snapshot = translate_payload(raw_day(DAY, payload))
    assert [p.time for p in snapshot.activity.steps_intraday] == ["08:15", "19:30"]
    assert snapshot.heart.hr_max == 96  # 昨天 23:50 的 120 bpm 不得混入


def test_physical_time_payload_uses_utc_offset():
    """回归：真实 v4 record 载荷只有物理时间（...Z）+ "28800s" 偏移，没有 civil*。

    曾导致：睡眠按 UTC 日期归属 → 当日睡眠为 None；睡眠/运动时间显示偏 8 小时。
    """
    payload = {
        "sleepRaw": {
            "dataPoints": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2026-10-26T17:03:00Z",
                            "startUtcOffset": "28800s",
                            "endTime": "2026-10-26T23:55:00Z",
                            "endUtcOffset": "28800s",
                        },
                        "type": "STAGES",
                        "metadata": {"stagesStatus": "SUCCEEDED", "processed": True, "mainSleep": True},
                        "summary": {
                            "minutesAsleep": "412",
                            "minutesInSleepPeriod": "440",
                            "minutesAwake": "28",
                            "minutesToFallAsleep": "4",
                            "minutesAfterWakeUp": "6",
                            "stagesSummary": [
                                {"type": "AWAKE", "minutes": "8", "count": "3"},
                                {"type": "LIGHT", "minutes": "184", "count": "12"},
                                {"type": "DEEP", "minutes": "90", "count": "6"},
                                {"type": "REM", "minutes": "138", "count": "5"},
                            ],
                        },
                        "stages": [
                            {
                                "startTime": "2026-10-26T17:03:00Z",
                                "startUtcOffset": "28800s",
                                "endTime": "2026-10-26T17:07:00Z",
                                "endUtcOffset": "28800s",
                                "type": "AWAKE",
                            },
                            {
                                "startTime": "2026-10-26T17:07:00Z",
                                "startUtcOffset": "28800s",
                                "endTime": "2026-10-26T18:37:00Z",
                                "endUtcOffset": "28800s",
                                "type": "DEEP",
                            },
                        ],
                    }
                }
            ]
        },
        "activitiesRaw": {
            "dataPoints": [
                {
                    "exercise": {
                        "interval": {
                            "startTime": "2026-10-26T16:30:00Z",
                            "startUtcOffset": "28800s",
                            "endTime": "2026-10-26T16:50:00Z",
                            "endUtcOffset": "28800s",
                        },
                        "exerciseType": "WALKING",
                        "metricsSummary": {"steps": "1200", "caloriesKcal": 80},
                    }
                },
                {
                    "exercise": {
                        "interval": {
                            "startTime": "2026-10-27T16:30:00Z",
                            "startUtcOffset": "28800s",
                            "endTime": "2026-10-27T16:50:00Z",
                            "endUtcOffset": "28800s",
                        },
                        "exerciseType": "WALKING",
                        "metricsSummary": {"steps": "900"},
                    }
                },
            ]
        },
        "stepsIntradayRaw": {
            "dataPoints": [
                {
                    "steps": {
                        "interval": {
                            "startTime": "2026-10-26T23:50:00Z",
                            "startUtcOffset": "28800s",
                            "endTime": "2026-10-26T23:55:00Z",
                            "endUtcOffset": "28800s",
                        },
                        "count": "120",
                    }
                },
                {
                    "steps": {
                        "interval": {
                            "startTime": "2026-10-26T15:58:00Z",
                            "startUtcOffset": "28800s",
                            "endTime": "2026-10-26T16:00:00Z",
                            "endUtcOffset": "28800s",
                        },
                        "count": "900",
                    }
                },
            ]
        },
    }
    raw = RawDay(date=REAL_DAY, source="google-health", generated_at="2026-10-27T08:10:00+08:00", payload=payload)
    snapshot = translate_payload(raw)

    assert snapshot.sleep is not None
    assert snapshot.sleep.is_main is True
    assert snapshot.sleep.total_minutes == 412
    assert snapshot.sleep.start_time == f"{REAL_DAY}T01:03:00+08:00"
    assert snapshot.sleep.end_time == f"{REAL_DAY}T07:55:00+08:00"
    assert snapshot.sleep.stages == {"wake": 8, "light": 184, "deep": 90, "rem": 138}
    assert snapshot.sleep.stage_timeline[0].start == f"{REAL_DAY}T01:03:00+08:00"

    assert [w.start_time for w in snapshot.workouts] == [f"{REAL_DAY}T00:30:00+08:00"]
    assert [p.time for p in snapshot.activity.steps_intraday] == ["07:50"]


def test_hr_intraday_bucketed_per_minute():
    """真实设备心率约 2–3 秒一个采样；按分钟聚合，避免单日快照过大。"""
    payload = base_payload()
    payload["heartIntradayRaw"] = {
        "dataPoints": [
            {
                "heartRate": {
                    "sampleTime": {"civilTime": civil(DAY, 9, 15), "physicalTime": f"{DAY}T01:15:10Z", "utcOffset": "28800s"},
                    "beatsPerMinute": 60,
                }
            },
            {
                "heartRate": {
                    "sampleTime": {"civilTime": civil(DAY, 9, 15), "physicalTime": f"{DAY}T01:15:40Z", "utcOffset": "28800s"},
                    "beatsPerMinute": 90,
                }
            },
            {
                "heartRate": {
                    "sampleTime": {"civilTime": civil(DAY, 9, 16), "physicalTime": f"{DAY}T01:16:05Z", "utcOffset": "28800s"},
                    "beatsPerMinute": 120,
                }
            },
        ]
    }
    snapshot = translate_payload(raw_day(DAY, payload))
    assert [(p.time, p.value) for p in snapshot.heart.hr_intraday] == [("09:15", 75.0), ("09:16", 120.0)]


def test_intraday_uses_single_dominant_source():
    """回归：同一分钟步数同时来自 Fitbit Air / MobileTrack / HealthKit 时不得累加。

    三个来源各记一部分步数，累加曾让分时合计变成约 3 倍（与日内总量对不上）。
    """
    payload = base_payload()
    payload["stepsIntradayRaw"] = {
        "dataPoints": [
            {
                "dataSource": {
                    "platform": "FITBIT",
                    "device": {"displayName": "Fitbit Air"},
                    "recordingMethod": "PASSIVELY_MEASURED",
                },
                "steps": {
                    "interval": {"civilStartTime": civil(DAY, 9, 0), "startTime": f"{DAY}T01:00:00Z"},
                    "count": "100",
                },
            },
            {
                "dataSource": {
                    "platform": "FITBIT",
                    "device": {"displayName": "MobileTrack"},
                    "recordingMethod": "PASSIVELY_MEASURED",
                },
                "steps": {
                    "interval": {"civilStartTime": civil(DAY, 9, 0), "startTime": f"{DAY}T01:00:00Z"},
                    "count": "90",
                },
            },
            {
                "dataSource": {"platform": "HEALTH_KIT", "recordingMethod": "PASSIVELY_MEASURED"},
                "steps": {
                    "interval": {"civilStartTime": civil(DAY, 9, 0), "startTime": f"{DAY}T01:00:00Z"},
                    "count": "80",
                },
            },
        ]
    }
    snapshot = translate_payload(raw_day(DAY, payload))
    assert [(p.time, p.value) for p in snapshot.activity.steps_intraday] == [("09:00", 100.0)]
