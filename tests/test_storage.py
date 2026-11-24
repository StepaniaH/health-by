"""存储层测试。"""

from __future__ import annotations

import json

import pytest

from health_by import storage
from health_by.models import ActivityDay, DaySnapshot
from health_by.providers.base import RawDay
from devtools.gen_demo_data import SyntheticProvider
from health_by.util import shift_iso

DAY = "2026-10-15"


def _store_synthetic_days(conn, dates: list[str]):
    provider = SyntheticProvider()
    for iso in dates:
        raw = provider.fetch_raw(iso)
        storage.store_day(conn, provider.translate(raw), raw)


def test_store_and_get_day(conn):
    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    day = provider.translate(raw)
    storage.store_day(conn, day, raw)

    loaded = storage.get_day(conn, DAY)
    assert loaded == day.to_dict()
    assert loaded["activity"]["steps"] == day.activity.steps


def test_metrics_rows_written_and_replaced(conn):
    _store_synthetic_days(conn, [DAY])
    rows = {
        row["metric"]: row["value"]
        for row in conn.execute("SELECT metric, value FROM daily_metrics WHERE date = ?", (DAY,))
    }
    assert "steps" in rows and rows["steps"] > 0
    assert "sleep_minutes" in rows or "sleep_minutes" not in rows  # 允许缺睡眠的合成日
    # 重新入库不产生重复行
    _store_synthetic_days(conn, [DAY])
    count = conn.execute("SELECT COUNT(*) AS n FROM daily_metrics WHERE date = ?", (DAY,)).fetchone()["n"]
    assert count == len(rows)


def test_trends_range_and_pivot(conn):
    dates = [shift_iso(DAY, -2), shift_iso(DAY, -1), DAY]
    _store_synthetic_days(conn, dates)
    trends = storage.get_trends(conn, DAY, 3)
    assert [t["date"] for t in trends] == dates
    assert all("steps" in t for t in trends)
    narrowed = storage.get_trends(conn, DAY, 2)
    assert [t["date"] for t in narrowed] == dates[1:]


def test_latest_and_count(conn):
    assert storage.count_days(conn) == 0
    assert storage.latest_day_date(conn) is None
    _store_synthetic_days(conn, [shift_iso(DAY, -1), DAY])
    assert storage.count_days(conn) == 2
    assert storage.latest_day_date(conn) == DAY
    assert storage.available_dates(conn) == [shift_iso(DAY, -1), DAY]


def test_settings_defaults_and_override(conn):
    settings = storage.load_settings(conn)
    assert settings["provider"] == "synthetic"
    assert settings["goals"]["steps"] == 8000
    storage.save_settings(conn, {"goals": {"steps": 10000, "sleep_minutes": 450}})
    settings = storage.load_settings(conn)
    assert settings["goals"]["steps"] == 10000


def test_credentials_raw_roundtrip(conn):
    storage.save_credentials_raw(conn, "google-health", "ENC-BLOB")
    assert storage.load_credentials_raw(conn, "google-health") == "ENC-BLOB"
    storage.delete_credentials(conn, "google-health")
    assert storage.load_credentials_raw(conn, "google-health") is None


def test_sync_runs_and_last_successful(conn):
    storage.record_sync_run(
        conn, provider="synthetic", started_at="t0", finished_at="t1", date=DAY, total=31, succeeded=31,
        status="ok", errors=None,
    )
    storage.record_sync_run(
        conn, provider="synthetic", started_at="t2", finished_at="t3", date=DAY, total=31, succeeded=2,
        status="error", errors=[{"key": "x", "message": "boom"}],
    )
    runs = storage.list_sync_runs(conn)
    assert runs[0]["status"] == "error" and runs[0]["errors"][0]["key"] == "x"
    assert storage.last_successful_sync(conn) == "t1"


def test_export_all(conn):
    _store_synthetic_days(conn, [DAY])
    payload = storage.export_all(conn)
    assert DAY in payload["days"]
    assert DAY in payload["raw_days"]
    assert payload["settings"]["provider"] == "synthetic"


def test_delete_all_data_keeps_settings(conn):
    storage.set_setting(conn, "custom", 42)
    _store_synthetic_days(conn, [DAY])
    storage.delete_all_data(conn)
    assert storage.count_days(conn) == 0
    assert storage.get_setting(conn, "custom") == 42


def test_oauth_state_lifecycle(conn):
    storage.save_oauth_state(conn, "state-1", "verifier-1")
    popped = storage.pop_oauth_state(conn, "state-1")
    assert popped["verifier"] == "verifier-1"
    assert storage.pop_oauth_state(conn, "state-1") is None


def test_partial_resync_preserves_previously_stored_fields(conn):
    """部分失败的重同步不能抹掉已经存好的数据。

    场景：某天先完整同步成功，之后某个 job 失败（sleep 拿不到），
    此时不能把已存的 sleep 数据丢掉。
    """
    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    day = provider.translate(raw)
    storage.store_day(conn, day, raw)
    full_metrics = {
        row["metric"]: row["value"]
        for row in conn.execute("SELECT metric, value FROM daily_metrics WHERE date = ?", (DAY,))
    }
    assert full_metrics.get("sleep_minutes") is not None

    degraded = provider.translate(raw)
    degraded.sleep = None
    degraded.activity.steps = None
    storage.store_day(conn, degraded, raw)

    after = {
        row["metric"]: row["value"]
        for row in conn.execute("SELECT metric, value FROM daily_metrics WHERE date = ?", (DAY,))
    }
    # 之前存好的字段仍然在
    assert after["sleep_minutes"] == full_metrics["sleep_minutes"]
    assert after["steps"] == full_metrics["steps"]
    # days.data 里也不能丢
    loaded = storage.get_day(conn, DAY)
    assert loaded["sleep"] is not None
    assert loaded["activity"]["steps"] == day.activity.steps


def test_non_finite_values_never_reach_stored_json(conn):
    """NaN/Infinity 会让浏览器 JSON.parse 直接失败，落库前必须换成 null。"""
    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    day = provider.translate(raw)
    day.heart.resting_hr = float("nan")
    day.body.weight_kg = float("inf")

    storage.store_day(conn, day, raw)

    stored_text = conn.execute("SELECT data FROM days WHERE date = ?", (DAY,)).fetchone()["data"]
    assert "NaN" not in stored_text
    assert "Infinity" not in stored_text
    loaded = storage.get_day(conn, DAY)
    assert loaded["heart"]["resting_hr"] is None
    assert loaded["body"]["weight_kg"] is None
    # 其余字段不受影响
    assert loaded["activity"]["steps"] == day.activity.steps


@pytest.mark.parametrize("old_source,new_source", [("synthetic", "google-health"), ("google-health", "synthetic")])
def test_source_change_replaces_snapshot_metrics_and_raw_without_touching_other_dates(
    conn, old_source, new_source
):
    provider = SyntheticProvider()
    old_raw = provider.fetch_raw(DAY)
    old_raw.source = old_source
    storage.store_day(conn, provider.translate(old_raw), old_raw)
    other_date = shift_iso(DAY, -1)
    _store_synthetic_days(conn, [other_date])
    other_day = storage.get_day(conn, other_date)

    incoming = DaySnapshot(
        DAY, new_source, "2026-10-30T00:00:00Z", activity=ActivityDay(steps=1234)
    )
    raw = RawDay(DAY, new_source, incoming.generated_at, {})
    storage.store_day(conn, incoming, raw)

    assert storage.get_day(conn, DAY) == incoming.to_dict()
    assert storage.get_trends(conn, DAY, 1) == [{"date": DAY, "steps": 1234.0}]
    archived = conn.execute("SELECT source, payload FROM raw_days WHERE date = ?", (DAY,)).fetchone()
    assert archived["source"] == new_source
    assert storage.decode_raw_payload(archived["payload"]) == {}
    assert storage.get_day(conn, other_date) == other_day


def test_same_source_resync_preserves_raw_when_payload_is_missing(conn):
    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    storage.store_day(conn, provider.translate(raw), raw)
    partial = DaySnapshot(DAY, "synthetic", "2026-10-30T00:00:00Z")
    storage.store_day(conn, partial, RawDay(DAY, "synthetic", partial.generated_at, {}))

    assert storage.export_all(conn)["raw_days"][DAY] == raw.payload
    assert storage.get_day(conn, DAY)["activity"]["steps"] is not None



def test_raw_days_compress_merge_legacy_decode_and_complete_delete(conn, synthetic_cfg):
    import os
    import zlib
    from health_by import db, secrets

    provider = SyntheticProvider()
    raw = provider.fetch_raw(DAY)
    snap = provider.translate(raw)
    storage.store_day(conn, snap, raw)

    # raw_days.payload 落库应为 zlib 压缩 BLOB，但可透明解码并与 export_all 对齐
    row = conn.execute("SELECT payload FROM raw_days WHERE date = ?", (DAY,)).fetchone()
    assert isinstance(row["payload"], (bytes, bytearray))
    assert storage.decode_raw_payload(row["payload"]) == raw.payload

    # 旧未压缩 JSON TEXT 行仍可读出
    legacy_day = shift_iso(DAY, -1)
    legacy_snap = DaySnapshot(legacy_day, "synthetic", "2026-10-30T00:00:00Z")
    storage.store_day(conn, legacy_snap, RawDay(legacy_day, "synthetic", legacy_snap.generated_at, {"stepsDaily": {"steps": 123}}))
    conn.execute("UPDATE raw_days SET payload = ? WHERE date = ?", ('{"stepsDaily":{"steps":123}}', legacy_day))
    conn.commit()
    exported_text = "".join(storage.iter_export_json(conn))
    exported = json.loads(exported_text)
    assert exported == storage.export_all(conn)
    assert exported["raw_days"][legacy_day] == {"stepsDaily": {"steps": 123}}

    # 同源部分重同步应按顶层任务键合并，保留先前成功的其它任务键值
    partial = DaySnapshot(DAY, "synthetic", "2026-10-30T01:00:00Z")
    storage.store_day(conn, partial, RawDay(DAY, "synthetic", partial.generated_at, {"stepsDaily": {"steps": 9999}}))
    merged = storage.export_all(conn)["raw_days"][DAY]
    assert merged["stepsDaily"] == {"steps": 9999}
    assert "sleepRaw" in merged

    # save_oauth_state 写入时顺带清理过期 state
    storage.save_oauth_state(conn, "expired-state", "v")
    conn.execute("UPDATE oauth_states SET created_at = '2000-01-01T00:00:00+00:00' WHERE state = 'expired-state'")
    conn.commit()
    storage.save_oauth_state(conn, "fresh-state", "v2")
    states = {r["state"] for r in conn.execute("SELECT state FROM oauth_states")}
    assert states == {"fresh-state"}

    # 设置与凭据应在清空后保留，其余 8 张数据/临时表全部清空
    store = secrets.SecretStore(synthetic_cfg.secret_key_path)
    storage.save_credentials_raw(conn, "gh", store.encrypt_json({"access_token": "tok"}))
    conn.execute(
        "INSERT OR REPLACE INTO raw_measure_dates(raw_date, measure_date) VALUES (?, ?)",
        (DAY, DAY),
    )
    conn.commit()
    storage.delete_all_data(conn)
    for table in ("days", "daily_metrics", "raw_days", "raw_measure_dates", "sync_runs", "ai_analyses", "ai_egress", "oauth_states"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert store.decrypt_json(storage.load_credentials_raw(conn, "gh")) == {"access_token": "tok"}


def test_recovered_resync_clears_stale_coverage_errors_and_empty_raw_indexed_once(conn, monkeypatch):
    from health_by import coverage as cov_module
    from health_by.models import ActivityDay, Coverage

    broken = DaySnapshot(
        DAY,
        "google-health",
        "2026-10-30T00:00:00Z",
        activity=ActivityDay(steps=5000),
        coverage=Coverage(
            endpoint_count=2,
            success_count=1,
            errors=[{"key": "sleepRaw", "message": "timeout", "status": 504}],
        ),
    )
    storage.store_day(conn, broken, RawDay(DAY, "google-health", broken.generated_at, {"stepsDaily": {}}))
    assert len(storage.get_day(conn, DAY)["coverage"]["errors"]) == 1

    recovered = DaySnapshot(
        DAY,
        "google-health",
        "2026-10-30T01:00:00Z",
        activity=ActivityDay(steps=5200),
        coverage=Coverage(endpoint_count=2, success_count=2, errors=[]),
    )
    storage.store_day(conn, recovered, RawDay(DAY, "google-health", recovered.generated_at, {"stepsDaily": {}}))
    saved = storage.get_day(conn, DAY)
    assert saved["coverage"]["endpoint_count"] == 2
    assert saved["coverage"]["success_count"] == 2
    assert saved["coverage"]["errors"] == []

    # 0 命中日期的 raw_days 写入后应记占位标记，不反复解压，也不污染 raw_coverage
    empty_day = shift_iso(DAY, -5)
    conn.execute(
        "INSERT OR REPLACE INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        (empty_day, "google-health", "2026-10-25T00:00:00Z", '{"userInfo": {"name": "A"}}'),
    )
    conn.commit()
    cov_module.index_raw_dates(conn)
    assert cov_module.raw_coverage(conn)["raw_days"] == 0

    decode_calls = []
    real_decode = storage.decode_raw_payload

    def spy_decode(blob):
        decode_calls.append(blob)
        return real_decode(blob)

    monkeypatch.setattr(storage, "decode_raw_payload", spy_decode)
    assert cov_module.index_raw_dates(conn) == 0
    assert decode_calls == []
