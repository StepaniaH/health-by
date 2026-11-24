"""Tests for offline legacy raw_days compression (`storage.compress_raw_days` and CLI)."""

from __future__ import annotations

import base64
import json
import sqlite3
import zlib
from pathlib import Path

from click.testing import CliRunner

from health_by import db as db_module, storage
from health_by.cli import main
from health_by.models import DaySnapshot
from health_by.providers.base import RawDay


def _seed_mixed_raw_days(db_path: Path) -> dict[str, dict]:
    conn = db_module.connect(db_path)
    db_module.migrate(conn)
    expected: dict[str, dict] = {}

    p1 = {"stepsDaily": {"values": [{"date": "2026-06-01", "steps": 8100}]}, "blob": "x" * 200}
    expected["2026-06-01"] = p1
    conn.execute(
        "INSERT INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        ("2026-06-01", "google-health", "2026-06-01T10:00:00Z", json.dumps(p1)),
    )

    p2 = {"stepsDaily": {"values": [{"date": "2026-06-02", "steps": 9200}]}, "blob": "y" * 200}
    expected["2026-06-02"] = p2
    b64 = "zlib+b64:" + base64.b64encode(zlib.compress(json.dumps(p2).encode("utf-8"))).decode("ascii")
    conn.execute(
        "INSERT INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        ("2026-06-02", "google-health", "2026-06-02T10:00:00Z", b64),
    )

    p3 = {"stepsDaily": {"values": [{"date": "2026-06-03", "steps": 10300}]}}
    expected["2026-06-03"] = p3
    storage.store_day(
        conn,
        DaySnapshot(date="2026-06-03", source="google-health", generated_at="2026-06-03T10:00:00Z"),
        RawDay(date="2026-06-03", source="google-health", generated_at="2026-06-03T10:00:00Z", payload=p3),
    )
    conn.commit()
    conn.close()
    return expected


def test_storage_compress_raw_days_dry_run_and_batch_conversion(tmp_path):
    db_path = tmp_path / "data" / "health-by.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    expected = _seed_mixed_raw_days(db_path)

    conn = db_module.connect(db_path)
    dry = storage.compress_raw_days(conn, batch_size=1, dry_run=True)
    assert dry["total"] == 3
    assert dry["converted"] == 2
    assert dry["skipped"] == 1
    assert isinstance(
        conn.execute("SELECT payload FROM raw_days WHERE date = '2026-06-01'").fetchone()["payload"],
        str,
    )

    applied = storage.compress_raw_days(conn, batch_size=1, dry_run=False)
    assert applied == dry

    for day, payload in expected.items():
        stored = conn.execute("SELECT payload FROM raw_days WHERE date = ?", (day,)).fetchone()["payload"]
        assert storage.is_compressed_raw_payload(stored) is True
        assert storage.decode_raw_payload(stored) == payload

    second = storage.compress_raw_days(conn, batch_size=2)
    assert second["total"] == 3
    assert second["converted"] == 0
    assert second["skipped"] == 3
    conn.close()


def test_cli_db_compress_raw_creates_0600_backup_and_is_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "health-by.db"
    expected = _seed_mixed_raw_days(db_path)

    runner = CliRunner()
    dry_res = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "db", "compress-raw", "--dry-run"])
    assert dry_res.exit_code == 0, dry_res.output
    assert "[dry-run]" in dry_res.output
    assert list(data_dir.glob("*.bak")) == []

    res = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "db", "compress-raw"])
    assert res.exit_code == 0, res.output
    assert "2 converted" in res.output
    assert "1 skipped" in res.output

    backups = list(data_dir.glob("health-by.db.pre-compress-*.bak"))
    assert len(backups) == 1
    backup_file = backups[0]
    assert (backup_file.stat().st_mode & 0o777) == 0o600

    # Verify backup preserves pre-compression readability and live DB passes integrity check.
    bak_conn = sqlite3.connect(backup_file)
    try:
        for day, payload in expected.items():
            row = bak_conn.execute("SELECT payload FROM raw_days WHERE date = ?", (day,)).fetchone()
            assert storage.decode_raw_payload(row[0]) == payload
    finally:
        bak_conn.close()

    live_conn = db_module.connect(db_path)
    try:
        assert live_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for day, payload in expected.items():
            row = live_conn.execute("SELECT payload FROM raw_days WHERE date = ?", (day,)).fetchone()
            assert storage.is_compressed_raw_payload(row["payload"]) is True
            assert storage.decode_raw_payload(row["payload"]) == payload
    finally:
        live_conn.close()

    rerun = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "db", "compress-raw", "--no-backup"])
    assert rerun.exit_code == 0, rerun.output
    assert "0 converted, 3 skipped" in rerun.output
