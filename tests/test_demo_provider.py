"""演示数据源测试：确定性、窗口一致性、值与管线合理性。"""

from __future__ import annotations

from devtools.gen_demo_data import DemoProvider, WINDOW_DAYS
from health_by.insights import pearson
from health_by.util import iso_range, shift_iso

DAY = "2026-10-15"


def test_deterministic_same_payload():
    provider = DemoProvider()
    first = provider.fetch_raw(DAY)
    second = provider.fetch_raw(DAY)
    assert first.payload == second.payload
    assert first.source == "demo"


def test_window_contains_expected_days():
    provider = DemoProvider()
    raw = provider.fetch_raw(DAY)
    points = raw.payload["stepsDaily"]["rollupDataPoints"]
    dates = [
        f"{p['civilStartTime']['date']['year']:04d}-{p['civilStartTime']['date']['month']:02d}-"
        f"{p['civilStartTime']['date']['day']:02d}"
        for p in points
    ]
    assert dates == iso_range(DAY, WINDOW_DAYS)
    assert len(dates) == 14


def test_overlapping_windows_consistent():
    """同一日期在不同窗口请求中的值必须一致（seed 按日期派生）。"""
    provider = DemoProvider()
    payload_a = provider.fetch_raw(DAY).payload
    payload_b = provider.fetch_raw(shift_iso(DAY, 1)).payload

    def value_for(payload):
        for point in payload["stepsDaily"]["rollupDataPoints"]:
            civil = point["civilStartTime"]["date"]
            if (civil["year"], civil["month"], civil["day"]) == (2026, 10, 15):
                return point["steps"]["countSum"]
        return None

    assert value_for(payload_a) == value_for(payload_b) is not None


def test_translate_pipeline_end_to_end():
    provider = DemoProvider()
    raw = provider.fetch_raw(DAY)
    snapshot = provider.translate(raw)
    assert snapshot.source == "demo"
    assert snapshot.date == DAY
    assert snapshot.coverage.endpoint_count == 31
    assert snapshot.coverage.success_count == 31
    assert snapshot.activity.steps is not None
    assert snapshot.device is not None and snapshot.profile.display_name == "演示用户"


def test_30_days_values_in_plausible_ranges():
    provider = DemoProvider()
    days_with_sleep = 0
    days_with_workouts = 0
    for iso in iso_range("2026-10-30", 30):
        snapshot = provider.translate(provider.fetch_raw(iso))
        steps = snapshot.activity.steps
        assert steps is None or 1000 < steps < 25000
        assert snapshot.activity.distance_km is None or 0 < snapshot.activity.distance_km < 25
        sleep = snapshot.sleep
        if sleep is not None:
            days_with_sleep += 1
            assert 320 <= (sleep.total_minutes or 0) <= 560
            assert sleep.stages.get("deep", 0) > 0
        if snapshot.workouts:
            days_with_workouts += 1
        assert snapshot.heart.resting_hr is None or 40 < snapshot.heart.resting_hr < 90
    assert days_with_sleep >= 25  # 约 6% 缺数据
    assert days_with_workouts >= 6  # 每周 2-3 次


def test_demo_recovery_couples_with_sleep():
    """演示数据的交叉关联：睡得更长的一天，静息心率更低、HRV 更高。"""
    provider = DemoProvider()
    sleep_minutes: list[float] = []
    resting_hr: list[float] = []
    hrv_values: list[float] = []
    for iso in iso_range("2026-10-30", 30):
        snapshot = provider.translate(provider.fetch_raw(iso))
        if snapshot.sleep is None or snapshot.heart.resting_hr is None or snapshot.heart.hrv_ms is None:
            continue
        sleep_minutes.append(float(snapshot.sleep.total_minutes or 0))
        resting_hr.append(float(snapshot.heart.resting_hr))
        hrv_values.append(float(snapshot.heart.hrv_ms))
    assert len(sleep_minutes) >= 24
    r_rhr = pearson(sleep_minutes, resting_hr)
    r_hrv = pearson(sleep_minutes, hrv_values)
    assert r_rhr is not None and r_rhr < -0.45, f"静息心率相关性异常：{r_rhr}"
    assert r_hrv is not None and r_hrv > 0.45, f"HRV 相关性异常：{r_hrv}"
