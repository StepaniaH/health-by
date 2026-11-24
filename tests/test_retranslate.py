"""Tests for offline `health-by retranslate` CLI."""

from __future__ import annotations

import json

from click.testing import CliRunner

from devtools.gen_demo_data import SyntheticProvider
from health_by import db as db_module, storage
from health_by.cli import main
from health_by.models import DaySnapshot
from health_by.providers.base import RawDay


def _seed_google_health_raw(data_dir, dates: list[str], *, corrupt_steps_in_days: int = 111) -> None:
    conn = db_module.connect(data_dir / "health-by.db")
    db_module.migrate(conn)
    provider = SyntheticProvider()
    for d in dates:
        payload = provider.fetch_raw(d).payload
        raw = RawDay(date=d, source="google-health", generated_at=f"{d}T12:00:00Z", payload=payload)
        # Intentionally store corrupted normalized snapshot & metric values to prove retranslate overwrites them.
        stale = DaySnapshot(date=d, source="google-health", generated_at="2020-01-01T00:00:00Z")
        stale.activity.steps = corrupt_steps_in_days
        storage.store_day(conn, stale, raw)
    conn.close()


def test_retranslate_date_range_and_all_offline(tmp_path, product_provider_defaults):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    dates = ["2026-06-10", "2026-06-11", "2026-06-12"]
    _seed_google_health_raw(data_dir, dates)

    conn = db_module.connect(data_dir / "health-by.db")
    raw_before = {
        r["date"]: r["payload"]
        for r in conn.execute("SELECT date, payload FROM raw_days ORDER BY date").fetchall()
    }
    conn.close()

    runner = CliRunner()
    dry = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--date", "2026-06-10", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "[dry-run]" in dry.output

    conn = db_module.connect(data_dir / "health-by.db")
    assert storage.get_day(conn, "2026-06-10")["activity"]["steps"] == 111
    conn.close()

    single = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--date", "2026-06-10"])
    assert single.exit_code == 0, single.output
    assert "1 updated" in single.output

    conn = db_module.connect(data_dir / "health-by.db")
    retranslated_10 = storage.get_day(conn, "2026-06-10")
    assert retranslated_10["activity"]["steps"] != 111
    assert retranslated_10["activity"]["steps"] > 1000
    assert storage.get_day(conn, "2026-06-11")["activity"]["steps"] == 111
    conn.close()

    rng = runner.invoke(
        main,
        ["--lang", "en", "--data", str(data_dir), "retranslate", "--start", "2026-06-11", "--end", "2026-06-12"],
    )
    assert rng.exit_code == 0, rng.output
    assert "2 updated" in rng.output

    conn = db_module.connect(data_dir / "health-by.db")
    for d in dates:
        snap = storage.get_day(conn, d)
        assert snap["activity"]["steps"] != 111
        metric_row = conn.execute(
            "SELECT value FROM daily_metrics WHERE date = ? AND metric = 'steps'", (d,)
        ).fetchone()
        assert metric_row is not None and metric_row["value"] == snap["activity"]["steps"]
        raw_row = conn.execute("SELECT payload FROM raw_days WHERE date = ?", (d,)).fetchone()
        assert raw_row["payload"] == raw_before[d]
    conn.close()


def test_retranslate_skips_unknown_source_and_validates_flags(tmp_path, product_provider_defaults):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db_module.connect(data_dir / "health-by.db")
    db_module.migrate(conn)
    storage.store_day(
        conn,
        DaySnapshot(date="2026-06-05", source="fitbit-export", generated_at="2026-06-05T12:00:00Z"),
        RawDay(date="2026-06-05", source="fitbit-export", generated_at="2026-06-05T12:00:00Z", payload={"steps": []}),
    )
    conn.close()

    runner = CliRunner()
    res = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--all"])
    assert res.exit_code == 0, res.output
    assert "1 skipped" in res.output

    bad_scope = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate"])
    assert bad_scope.exit_code != 0

    ambiguous = runner.invoke(
        main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--all", "--date", "2026-06-05"]
    )
    assert ambiguous.exit_code != 0

    bad_date = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--date", "not-a-date"])
    assert bad_date.exit_code != 0


def test_retranslate_reports_corrupt_payload_with_nonzero_exit(tmp_path, product_provider_defaults):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db_module.connect(data_dir / "health-by.db")
    db_module.migrate(conn)
    conn.execute(
        "INSERT INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        ("2026-06-01", "google-health", "2026-06-01T00:00:00Z", "{invalid-json"),
    )
    conn.commit()
    conn.close()

    runner = CliRunner()
    res = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--all"])
    assert res.exit_code == 1
    assert "1 failed" in res.output


def test_retranslate_preserves_existing_coverage_errors(tmp_path, product_provider_defaults):
    from health_by.models import Coverage

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db_module.connect(data_dir / "health-by.db")
    db_module.migrate(conn)
    provider = SyntheticProvider()
    d = "2026-06-15"
    payload = provider.fetch_raw(d).payload
    payload.pop("sleepRaw", None)
    errors = [{"key": "sleepRaw", "message": "403 forbidden", "status": 403}]
    raw = RawDay(date=d, source="google-health", generated_at=f"{d}T12:00:00Z", payload=payload, errors=errors)
    snap = DaySnapshot(
        date=d,
        source="google-health",
        generated_at=f"{d}T12:00:00Z",
        coverage=Coverage(endpoint_count=30, success_count=29, errors=errors),
    )
    storage.store_day(conn, snap, raw)
    conn.close()

    runner = CliRunner()
    res = runner.invoke(main, ["--lang", "en", "--data", str(data_dir), "retranslate", "--date", d])
    assert res.exit_code == 0, res.output

    conn = db_module.connect(data_dir / "health-by.db")
    updated = storage.get_day(conn, d)
    conn.close()
    assert updated["coverage"]["endpoint_count"] == 30
    assert updated["coverage"]["success_count"] == 29
    assert updated["coverage"]["errors"] == errors
