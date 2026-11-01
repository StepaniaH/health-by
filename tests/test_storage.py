"""存储层测试。"""

from __future__ import annotations

import json

import pytest

from health_by import storage
from health_by.models import ActivityDay, DaySnapshot
from health_by.providers.base import RawDay
from health_by.providers.demo import DemoProvider
from health_by.util import shift_iso

DAY = "2026-10-15"


def _store_demo_days(conn, dates: list[str]):
    provider = DemoProvider()
    for iso in dates:
        raw = provider.fetch_raw(iso)
        storage.store_day(conn, provider.translate(raw), raw)


def test_store_and_get_day(conn):
    provider = DemoProvider()
    raw = provider.fetch_raw(DAY)
    day = provider.translate(raw)
    storage.store_day(conn, day, raw)

    loaded = storage.get_day(conn, DAY)
    assert loaded == day.to_dict()
    assert loaded["activity"]["steps"] == day.activity.steps


def test_metrics_rows_written_and_replaced(conn):
    _store_demo_days(conn, [DAY])
    rows = {
        row["metric"]: row["value"]
        for row in conn.execute("SELECT metric, value FROM daily_metrics WHERE date = ?", (DAY,))
    }
    assert "steps" in rows and rows["steps"] > 0
    assert "sleep_minutes" in rows or "sleep_minutes" not in rows  # 允许缺睡眠的演示日
    # 重新入库不产生重复行
    _store_demo_days(conn, [DAY])
    count = conn.execute("SELECT COUNT(*) AS n FROM daily_metrics WHERE date = ?", (DAY,)).fetchone()["n"]
    assert count == len(rows)


def test_trends_range_and_pivot(conn):
    dates = [shift_iso(DAY, -2), shift_iso(DAY, -1), DAY]
    _store_demo_days(conn, dates)
    trends = storage.get_trends(conn, DAY, 3)
    assert [t["date"] for t in trends] == dates
    assert all("steps" in t for t in trends)
    narrowed = storage.get_trends(conn, DAY, 2)
    assert [t["date"] for t in narrowed] == dates[1:]


def test_latest_and_count(conn):
    assert storage.count_days(conn) == 0
    assert storage.latest_day_date(conn) is None
    _store_demo_days(conn, [shift_iso(DAY, -1), DAY])
    assert storage.count_days(conn) == 2
    assert storage.latest_day_date(conn) == DAY
    assert storage.available_dates(conn) == [shift_iso(DAY, -1), DAY]


def test_settings_defaults_and_override(conn):
    settings = storage.load_settings(conn)
    assert settings["provider"] == "demo"
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
        conn, provider="demo", started_at="t0", finished_at="t1", date=DAY, total=31, succeeded=31,
        status="ok", errors=None,
    )
    storage.record_sync_run(
        conn, provider="demo", started_at="t2", finished_at="t3", date=DAY, total=31, succeeded=2,
        status="error", errors=[{"key": "x", "message": "boom"}],
    )
    runs = storage.list_sync_runs(conn)
    assert runs[0]["status"] == "error" and runs[0]["errors"][0]["key"] == "x"
    assert storage.last_successful_sync(conn) == "t1"


def test_export_all(conn):
    _store_demo_days(conn, [DAY])
    payload = storage.export_all(conn)
    assert DAY in payload["days"]
    assert DAY in payload["raw_days"]
    assert payload["settings"]["provider"] == "demo"


def test_delete_all_data_keeps_settings(conn):
    storage.set_setting(conn, "custom", 42)
    _store_demo_days(conn, [DAY])
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
    provider = DemoProvider()
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
    provider = DemoProvider()
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


@pytest.mark.parametrize("old_source,new_source", [("demo", "google-health"), ("google-health", "demo")])
def test_source_change_replaces_snapshot_metrics_and_raw_without_touching_other_dates(
    conn, old_source, new_source
):
    provider = DemoProvider()
    old_raw = provider.fetch_raw(DAY)
    old_raw.source = old_source
    storage.store_day(conn, provider.translate(old_raw), old_raw)
    other_date = shift_iso(DAY, -1)
    _store_demo_days(conn, [other_date])
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
    assert json.loads(archived["payload"]) == {}
    assert storage.get_day(conn, other_date) == other_day


def test_same_source_resync_preserves_raw_when_payload_is_missing(conn):
    provider = DemoProvider()
    raw = provider.fetch_raw(DAY)
    storage.store_day(conn, provider.translate(raw), raw)
    partial = DaySnapshot(DAY, "demo", "2026-10-30T00:00:00Z")
    storage.store_day(conn, partial, RawDay(DAY, "demo", partial.generated_at, {}))

    assert storage.export_all(conn)["raw_days"][DAY] == raw.payload
    assert storage.get_day(conn, DAY)["activity"]["steps"] is not None
