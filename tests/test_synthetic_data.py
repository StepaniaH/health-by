"""合成测试数据源测试：确定性、窗口一致性、值与管线合理性。"""

from __future__ import annotations

from devtools.gen_demo_data import SyntheticProvider, WINDOW_DAYS
from health_by.insights import pearson
from health_by.util import iso_range, shift_iso

DAY = "2026-10-15"


def test_deterministic_same_payload():
    provider = SyntheticProvider()
    first = provider.fetch_raw(DAY)
    second = provider.fetch_raw(DAY)
    assert first.payload == second.payload
    assert first.source == "synthetic"


def test_window_contains_expected_days():
    provider = SyntheticProvider()
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
    provider = SyntheticProvider()
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
    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    snapshot = provider.translate(raw)
    assert snapshot.source == "synthetic"
    assert snapshot.date == DAY
    assert snapshot.coverage.endpoint_count == 31
    assert snapshot.coverage.success_count == 31
    assert snapshot.activity.steps is not None
    assert snapshot.device is not None and snapshot.profile.display_name == "Synthetic user"


def test_30_days_values_in_plausible_ranges():
    provider = SyntheticProvider()
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


def test_synthetic_recovery_couples_with_sleep():
    """合成数据的交叉关联：睡得更长的一天，静息心率更低、HRV 更高。"""
    provider = SyntheticProvider()
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


def test_local_data_command_creates_an_unregistered_source_archive(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    from health_by import db, storage
    from health_by.config import Config

    root = Path(__file__).resolve().parent.parent
    data_dir = tmp_path / "dev-data"
    env = {**os.environ, "PYTHONPATH": str(root)}
    result = subprocess.run(
        [sys.executable, "-B", "-m", "devtools.make_dev_data", "--days", "2", "--out", str(data_dir)],
        cwd=root, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    connection = db.connect(Config(data_dir=data_dir).db_path)
    try:
        assert storage.count_days(connection) == 2
        assert storage.get_setting(connection, "provider") == "google-health"
        # 开发库要和真实部署长得一样：行数据也标成正式来源。
    assert {row[0] for row in connection.execute("SELECT DISTINCT source FROM days")} == {"google-health"}
    finally:
        connection.close()
    # A new process must read the archive without registering the development source.
    result = subprocess.run(
        [sys.executable, "-B", "-m", "health_by.cli", "--data", str(data_dir), "status"],
        cwd=root, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "google-health" in result.stdout
