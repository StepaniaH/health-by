"""Tests for Fitbit / Google Takeout offline importer and `health-by import-fitbit` CLI."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from health_by import db as db_module, storage
from health_by.cli import main
from health_by.importers import fitbit_zip


def _sample_fitbit_files() -> dict[str, str]:
    return {
        "Takeout/Fitbit/Physical Activity/steps-2026-06-01.json": json.dumps([
            {"dateTime": "06/01/26 08:15:00", "value": "1200"},
            {"dateTime": "06/01/26 18:30:00", "value": "7050"},
        ]),
        "Takeout/Fitbit/Physical Activity/calories-2026-06-01.json": json.dumps([
            {"dateTime": "06/01/26 00:00:00", "value": "2140.5"},
        ]),
        "Takeout/Fitbit/Physical Activity/distance-2026-06-01.json": json.dumps([
            {"dateTime": "06/01/26 08:15:00", "value": "100000"},
            {"dateTime": "06/01/26 18:30:00", "value": "520000"},
        ]),
        "Takeout/Fitbit/Heart Rate/resting_heart_rate-2026-06-01.json": json.dumps([
            {"dateTime": "06/01/26 00:00:00", "value": {"date": "06/01/26", "value": 58.6, "error": 1.2}},
        ]),
        "Takeout/Fitbit/Sleep/sleep-2026-06-01.json": json.dumps([
            {
                "dateOfSleep": "2026-06-01",
                "startTime": "2026-05-31T23:10:00.000",
                "endTime": "2026-06-01T07:00:00.000",
                "minutesAsleep": 430,
                "timeInBed": 470,
                "efficiency": 91,
                "minutesAwake": 40,
                "minutesToFallAsleep": 8,
                "minutesAfterWakeup": 2,
                "mainSleep": True,
                "levels": {
                    "summary": {
                        "deep": {"minutes": 85},
                        "light": {"minutes": 240},
                        "rem": {"minutes": 105},
                        "wake": {"minutes": 40},
                    },
                    "data": [
                        {"dateTime": "2026-05-31T23:10:00.000", "level": "light", "seconds": 1800},
                        {"dateTime": "2026-06-01T01:00:00.000", "level": "deep", "seconds": 2400},
                    ],
                },
            },
            {
                "dateOfSleep": "2026-06-01",
                "startTime": "2026-06-01T13:30:00.000",
                "endTime": "2026-06-01T14:00:00.000",
                "minutesAsleep": 25,
                "timeInBed": 30,
                "mainSleep": False,
            },
        ]),
        "Takeout/Fitbit/Body/weight-2026-06-01.json": json.dumps([
            {"date": "06/01/26", "weight": 154.32, "fat": 18.4},
        ]),
        "Takeout/Fitbit/Physical Activity/exercise-2026-06-01.json": json.dumps([
            {
                "activityName": "Run",
                "startTime": "06/01/26 18:10:00",
                "activeDuration": 1800000,
                "calories": 320,
                "distance": 5.0,
                "distanceUnit": "Kilometer",
                "averageHeartRate": 148,
                "steps": 5200,
            }
        ]),
        "Takeout/Fitbit/Heart Rate Variability/Daily Heart Rate Variability Summary - 2026-06-01.csv": (
            "timestamp,rmssd,nremhr,entropy\n2026-06-01T00:00:00,46.8,56.2,2.1\n"
        ),
        "Takeout/Fitbit/Oxygen Saturation (SpO2)/Daily SpO2 - 2026-06-01.csv": (
            "timestamp,average_value,lower_bound,upper_bound\n2026-06-01T00:00:00,97.4,94.0,99.0\n"
        ),
        "Takeout/Fitbit/Respiratory Rate/Daily Respiratory Rate Summary - 2026-06-01.csv": (
            "timestamp,daily_respiratory_rate\n2026-06-01T00:00:00,14.6\n"
        ),
        "Takeout/Fitbit/Temperature/Computed Temperature - 2026-06-01.csv": (
            "type,sleep_start,sleep_end,nightly_temperature,baseline_relative_sample_sum\n"
            "SKIN,2026-05-31T23:10,2026-06-01T07:00,33.42,0.24\n"
        ),
    }


def _build_zip(dest: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return dest


def test_import_fitbit_zip_and_directory_and_overwrite(tmp_path):
    zip_path = _build_zip(tmp_path / "fitbit-export.zip", _sample_fitbit_files())
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    dry = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(zip_path), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "[dry-run]" in dry.output
    assert "would import 1" in dry.output
    assert not (data_dir / "health-by.db").exists()

    imported = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(zip_path)])
    assert imported.exit_code == 0, imported.output
    assert "imported 1" in imported.output

    conn = db_module.connect(data_dir / "health-by.db")
    day = storage.get_day(conn, "2026-06-01")
    assert day is not None
    assert day["source"] == "fitbit-export"
    assert day["activity"]["steps"] == 8250
    assert len(day["activity"]["steps_intraday"]) == 2
    assert day["activity"]["calories_out"] == 2140.5
    assert day["activity"]["distance_km"] == 6.2
    assert day["heart"]["resting_hr"] == 59
    assert day["heart"]["hrv_ms"] == 46.8
    assert day["heart"]["spo2"] == 97.4
    assert day["heart"]["breathing_rate"] == 14.6
    assert day["heart"]["skin_temp_nightly_c"] == 33.42
    assert day["heart"]["skin_temp_delta_c"] == 0.24
    assert day["sleep"]["total_minutes"] == 430
    assert day["sleep"]["stages"]["deep"] == 85
    assert day["naps_count"] == 1 and day["naps_minutes"] == 25
    assert day["body"]["weight_kg"] == 70.0  # 154.32 lb -> 70.0 kg
    assert day["body"]["body_fat_pct"] == 18.4
    assert len(day["workouts"]) == 1 and day["workouts"][0]["name"] == "Run"
    conn.close()

    # Re-importing same archive skips existing date unless --overwrite is passed.
    skipped = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(zip_path)])
    assert skipped.exit_code == 0, skipped.output
    assert "skipped existing 1" in skipped.output

    # Test extracted directory with --weight-unit kg and --overwrite.
    folder = tmp_path / "extracted"
    for rel, content in _sample_fitbit_files().items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    overwrite_res = runner.invoke(
        main,
        ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(folder), "--overwrite", "--weight-unit", "kg"],
    )
    assert overwrite_res.exit_code == 0, overwrite_res.output
    assert "imported 1" in overwrite_res.output

    conn = db_module.connect(data_dir / "health-by.db")
    day_kg = storage.get_day(conn, "2026-06-01")
    assert day_kg["body"]["weight_kg"] == 154.3
    conn.close()


@pytest.mark.parametrize(
    "bad_name",
    [
        "../steps-2026-06-01.json",
        "/etc/steps-2026-06-01.json",
        "Takeout/../../steps-2026-06-01.json",
        "C:steps-2026-06-01.json",
        "Takeout\\steps-2026-06-01.json",
    ],
)
def test_import_fitbit_rejects_unsafe_paths(tmp_path, bad_name):
    zip_path = _build_zip(tmp_path / "unsafe.zip", {bad_name: "[]"})
    with pytest.raises(ValueError):
        fitbit_zip.parse_fitbit_export(zip_path)


def test_import_fitbit_rejects_symlinks_and_oversized_members(tmp_path, monkeypatch):
    sym_zip = tmp_path / "sym.zip"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        info = zipfile.ZipInfo("steps-2026-06-01.json")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError):
        fitbit_zip.parse_fitbit_export(sym_zip)

    big_zip = _build_zip(
        tmp_path / "big.zip",
        {"steps-2026-06-01.json": json.dumps([{"dateTime": "06/01/26", "value": "1000"}])},
    )
    monkeypatch.setattr(fitbit_zip, "MAX_MEMBER_BYTES", 10)
    with pytest.raises(ValueError):
        fitbit_zip.parse_fitbit_export(big_zip)


def test_fitbit_distance_no_cliff_true_overwrite_and_corrupt_json_count(tmp_path):
    """Fitbit 导入：999/1000 cm 连续换算无悬崖、--overwrite 真覆盖清旧字段、坏 JSON 不计入 files_parsed。"""
    # 1. 距离单位平滑换算：整数 999 cm 与 1000 cm 均约 0.01 km，显式小数 6.25 为 6.25 km
    dist_zip = _build_zip(
        tmp_path / "dist.zip",
        {
            "Takeout/Fitbit/Global Export Data/distance-2026-06-01.json": json.dumps([
                {"dateTime": "06/01/26 08:00:00", "value": "999"},
                {"dateTime": "06/02/26 08:00:00", "value": "1000"},
                {"dateTime": "06/03/26 08:00:00", "value": "6.25"},
            ]),
        },
    )
    dist_snaps, _ = fitbit_zip.parse_fitbit_export(dist_zip)
    by_date = {snap.date: snap for snap, _ in dist_snaps}
    assert by_date["2026-06-01"].activity.distance_km == 0.01
    assert by_date["2026-06-02"].activity.distance_km == 0.01
    assert by_date["2026-06-03"].activity.distance_km == 6.25

    # 2. 损坏的 JSON 文件不计入 files_parsed，记入 files_failed
    corrupt_zip = _build_zip(
        tmp_path / "corrupt.zip",
        {"Takeout/Fitbit/Global Export Data/steps-2026-06-01.json": "{not-valid-json"},
    )
    counts: dict[str, int] = {}
    corrupt_snaps, files_parsed = fitbit_zip.parse_fitbit_export(corrupt_zip, file_counts=counts)
    assert corrupt_snaps == []
    assert files_parsed == 0
    assert counts["files_recognized"] == 1
    assert counts["files_parsed"] == 0
    assert counts["files_failed"] == 1

    # 3. --overwrite 必须整日替换，清除旧数据中已不存在的字段（如 distance_km）
    data_dir = tmp_path / "overwrite-data"
    full_zip = _build_zip(tmp_path / "full.zip", _sample_fitbit_files())
    steps_only_zip = _build_zip(
        tmp_path / "steps-only.zip",
        {
            "Takeout/Fitbit/Global Export Data/steps-2026-06-01.json": json.dumps([
                {"dateTime": "06/01/26 08:00:00", "value": "3333"},
            ]),
        },
    )
    runner = CliRunner()
    assert runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(full_zip)]).exit_code == 0
    assert (
        runner.invoke(
            main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(steps_only_zip), "--overwrite"]
        ).exit_code
        == 0
    )
    conn = db_module.connect(data_dir / "health-by.db")
    try:
        replaced = storage.get_day(conn, "2026-06-01")
        assert replaced["activity"]["steps"] == 3333
        assert replaced["activity"]["distance_km"] is None
        assert replaced["sleep"] is None
    finally:
        conn.close()


def test_fitbit_overwrite_blocks_when_archive_has_corrupt_files(tmp_path):
    """好坏文件并存时，--overwrite 必须中止并保留已有好数据，--dry-run 也要报非零退出码。"""
    data_dir = tmp_path / "protect-data"
    full_zip = _build_zip(tmp_path / "full.zip", _sample_fitbit_files())
    mixed_zip = _build_zip(
        tmp_path / "mixed-corrupt.zip",
        {
            "Takeout/Fitbit/Global Export Data/steps-2026-06-01.json": json.dumps([
                {"dateTime": "06/01/26 08:00:00", "value": "9999"},
            ]),
            "Takeout/Fitbit/Global Export Data/distance-2026-06-01.json": "{broken-distance-json",
        },
    )
    runner = CliRunner()
    ok = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(full_zip)])
    assert ok.exit_code == 0, ok.output

    # --dry-run 发现坏文件应报非零并打印失败文件名
    dry = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(mixed_zip), "--overwrite", "--dry-run"]
    )
    assert dry.exit_code != 0
    assert "distance-2026-06-01.json" in dry.output

    # 正式 --overwrite 必须拒绝覆盖，原有的 8250 步与 6.2 km 毫发无损
    blocked = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(mixed_zip), "--overwrite"]
    )
    assert blocked.exit_code != 0
    assert "distance-2026-06-01.json" in blocked.output

    conn = db_module.connect(data_dir / "health-by.db")
    try:
        kept = storage.get_day(conn, "2026-06-01")
        assert kept["activity"]["steps"] == 8250
        assert kept["activity"]["distance_km"] == 6.2
    finally:
        conn.close()


@pytest.mark.parametrize(
    "bad_csv",
    [
        'timestamp,rmssd,coverage,low_frequency,high_frequency\n"2026-06-01T00:00:00",43.0,"unclosed',
        "timestamp,rmssd,coverage,low_frequency,high_frequency\n2026-06-01T00:00:00,43.0",
        "timestamp,rm",
    ],
)
def test_fitbit_overwrite_blocks_on_truncated_csv_quoted_and_unquoted(tmp_path, bad_csv):
    """严格 CSV 解析与列截断检测：未闭合引号或半行截断的 CSV 必须阻止 --overwrite 并保留已有 HRV 与原始记录。"""
    data_dir = tmp_path / "protect-csv-data"
    full_zip = _build_zip(tmp_path / "full.zip", _sample_fitbit_files())
    hrv_name = "Daily Heart Rate Variability Summary - 2026-06-01.csv"
    corrupt_csv_zip = _build_zip(
        tmp_path / "mixed-csv-corrupt.zip",
        {
            "Takeout/Fitbit/Global Export Data/steps-2026-06-01.json": json.dumps([
                {"dateTime": "06/01/26 08:00:00", "value": "9999"},
            ]),
            f"Takeout/Fitbit/Heart Rate Variability/{hrv_name}": bad_csv,
        },
    )
    runner = CliRunner()
    assert runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(full_zip)]).exit_code == 0

    dry = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(corrupt_csv_zip), "--overwrite", "--dry-run"]
    )
    assert dry.exit_code != 0
    assert hrv_name in dry.output

    blocked = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(corrupt_csv_zip), "--overwrite"]
    )
    assert blocked.exit_code != 0
    assert hrv_name in blocked.output

    conn = db_module.connect(data_dir / "health-by.db")
    try:
        kept = storage.get_day(conn, "2026-06-01")
        assert kept["activity"]["steps"] == 8250
        assert kept["activity"]["distance_km"] == 6.2
        assert kept["heart"]["hrv_ms"] == 46.8
        raw_exported = storage.export_all(conn)["raw_days"]["2026-06-01"]
        assert "hrv_csv" in raw_exported
    finally:
        conn.close()


def test_fitbit_csv_header_validation_and_valid_empty_csv(tmp_path):
    """完整表头的空 CSV 应正常通过；各类型截断在表头中间的 CSV 必须记为失败。"""
    data_dir = tmp_path / "empty-valid-csv-data"
    valid_empty_zip = _build_zip(
        tmp_path / "valid-empty-csv.zip",
        {
            "Takeout/Fitbit/Global Export Data/steps-2026-06-01.json": json.dumps([
                {"dateTime": "06/01/26 08:00:00", "value": "4321"},
            ]),
            "Takeout/Fitbit/Heart Rate Variability/Daily Heart Rate Variability Summary - 2026-06-01.csv": (
                "timestamp,rmssd,nremhr,entropy\n"
            ),
        },
    )
    counts: dict = {}
    snaps, files_parsed = fitbit_zip.parse_fitbit_export(valid_empty_zip, file_counts=counts)
    assert files_parsed == 2
    assert counts["files_failed"] == 0
    assert len(snaps) == 1 and snaps[0][0].activity.steps == 4321

    runner = CliRunner()
    res = runner.invoke(
        main,
        ["--lang", "en", "--data", str(data_dir), "import-fitbit", str(valid_empty_zip), "--overwrite"],
    )
    assert res.exit_code == 0, res.output

    truncated_headers = {
        "Takeout/Fitbit/Oxygen Saturation (SpO2)/Daily SpO2 - 2026-06-01.csv": "timestamp,avg",
        "Takeout/Fitbit/Respiratory Rate/Daily Respiratory Rate Summary - 2026-06-01.csv": "timestamp,daily_resp",
        "Takeout/Fitbit/Temperature/Computed Temperature - 2026-06-01.csv": "type,sleep_start,sleep_end,nightly_temp",
    }
    for rel_path, bad_header in truncated_headers.items():
        bad_zip = _build_zip(tmp_path / f"bad-{Path(rel_path).stem}.zip", {rel_path: bad_header})
        bad_counts: dict = {}
        bad_snaps, bad_parsed = fitbit_zip.parse_fitbit_export(bad_zip, file_counts=bad_counts)
        assert bad_snaps == []
        assert bad_parsed == 0
        assert bad_counts["files_failed"] == 1
        assert rel_path in bad_counts["failed_files"]
