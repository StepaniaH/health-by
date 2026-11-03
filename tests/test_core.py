"""config / db / secrets / models 基础测试。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

from health_by.config import DEFAULT_PORT, load_config
from health_by.db import SCHEMA_VERSION, connect, migrate
from health_by.models import DaySnapshot, metric_values
from health_by.secrets import SecretError, SecretStore
from health_by.util import iso_range, shift_iso


def test_load_config_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_BY_DATA", str(tmp_path / "d"))
    cfg = load_config()
    assert cfg.data_dir == tmp_path / "d"
    assert cfg.port == DEFAULT_PORT
    assert cfg.data_dir.exists()


def test_load_config_explicit_dir_wins(tmp_path):
    cfg = load_config(tmp_path / "explicit", port=1234, host="0.0.0.0")
    assert cfg.data_dir == tmp_path / "explicit"
    assert cfg.port == 1234
    assert cfg.host == "0.0.0.0"


def test_shift_and_range():
    assert shift_iso("2026-10-15", -14) == "2026-10-01"
    assert shift_iso("2026-01-01", -1) == "2025-12-31"
    rng = iso_range("2026-10-15", 3)
    assert rng == ["2026-10-13", "2026-10-14", "2026-10-15"]


def test_migrate_idempotent(tmp_path):
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    migrate(conn)
    migrate(conn)  # 二次迁移不报错
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row["value"]) == SCHEMA_VERSION
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "meta",
        "settings",
        "credentials",
        "days",
        "daily_metrics",
        "raw_days",
        "sync_runs",
        "oauth_states",
    } <= tables
    conn.close()


def test_sqlite_cross_thread(tmp_path):
    """FastAPI 线程池下跨线程使用连接不得抛 ProgrammingError。"""
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    errors: list[Exception] = []

    def worker():
        try:
            conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
            conn.commit()
            conn.execute("SELECT value FROM settings WHERE key='k'").fetchone()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == []
    conn.close()


def test_wal_enabled(tmp_path):
    conn = connect(tmp_path / "t.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()


def test_secret_store_roundtrip(tmp_path):
    store = SecretStore(tmp_path / "secret.key")
    payload = {"client_secret": "s3cr3t", "tokens": {"refresh_token": "r"}}
    token = store.encrypt_json(payload)
    assert "s3cr3t" not in token
    assert store.decrypt_json(token) == payload


def test_secret_key_permissions_and_reuse(tmp_path):
    key_path = tmp_path / "secret.key"
    store = SecretStore(key_path)
    store.encrypt_json({"a": 1})
    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o600
    # 新实例复用同一密钥可解密
    store2 = SecretStore(key_path)
    token = store.encrypt_json({"b": 2})
    assert store2.decrypt_json(token) == {"b": 2}


def test_secret_store_wrong_key_fails(tmp_path):
    store = SecretStore(tmp_path / "k1.key")
    token = store.encrypt_json({"x": 1})
    other = SecretStore(tmp_path / "k2.key")
    try:
        other.decrypt_json(token)
        raise AssertionError("expected SecretError")
    except SecretError:
        pass


def test_metric_values_skips_none():
    day = DaySnapshot(date="2026-10-15", source="synthetic", generated_at="t")
    day.activity.steps = 8000
    day.activity.calories_out = 2100.0
    values = metric_values(day)
    assert values == {"steps": 8000.0, "calories_out": 2100.0}


def test_metric_values_skips_nan():
    """NaN 绑定到 SQLite 会变成 NULL，触发 daily_metrics.value 的 NOT NULL。"""
    day = DaySnapshot(date="2026-10-15", source="synthetic", generated_at="t")
    day.activity.steps = float("nan")  # type: ignore[assignment]
    day.heart.resting_hr = 60
    assert metric_values(day) == {"resting_hr": 60.0}


def test_day_snapshot_serializable():
    day = DaySnapshot(date="2026-10-15", source="synthetic", generated_at="t")
    blob = json.dumps(day.to_dict())
    assert "2026-10-15" in blob


def _build_legacy_database(db_path, version: int):
    """手工把一个库建到指定的旧版本，模拟「已经在用、还没升级」的库。"""
    from health_by.db import MIGRATIONS

    conn = connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for block_version, sql in MIGRATIONS:
        if block_version <= version:
            conn.executescript(sql)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(version),))
    conn.commit()
    return conn


def test_upgrade_from_v1_keeps_existing_data(tmp_path):
    """v1 → 最新版本的真实升级路径。

    这条测的是**唯一会发生在维护者身上的路径**：他的库早就在用（v1），
    明天重启时跑的是 ALTER TABLE 追加列，而不是从空库一路建到最新。
    其余用例都从空库开始建，覆盖不到"已经有数据的库能否平滑升级"。
    """
    from health_by.db import MIGRATIONS

    conn = _build_legacy_database(tmp_path / "legacy.db", 1)
    # 灌真实形状的数据：一天多条 metric（主键是 date+metric），加一条同步记录
    conn.executemany(
        "INSERT INTO daily_metrics (date, metric, value) VALUES (?, ?, ?)",
        [("2026-10-29", "steps", 8213.0), ("2026-10-29", "sleep_minutes", 402.0),
         ("2026-10-29", "resting_hr", 54.0), ("2026-10-28", "steps", 6120.0)],
    )
    conn.execute(
        "INSERT INTO days (date, source, generated_at, data) VALUES (?, ?, ?, ?)",
        ("2026-10-29", "google-health", "2026-10-29T00:00:00+00:00", '{"date": "2026-10-29"}'),
    )
    conn.execute(
        "INSERT INTO sync_runs (provider, started_at, finished_at, date, total, succeeded, status) "
        "VALUES ('google-health', 'a', 'b', '2026-10-29', 31, 31, 'ok')"
    )
    conn.commit()
    before = [dict(r) for r in conn.execute("SELECT date, metric, value FROM daily_metrics ORDER BY date, metric")]

    migrate(conn)

    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row["value"]) == SCHEMA_VERSION

    # 新增列必须都在（v2 的 warnings、v3 的 fields）
    legacy_columns = {r["name"] for r in conn.execute("PRAGMA table_info(sync_runs)")}
    assert {"warnings", "fields"} <= legacy_columns
    # SCHEMA_VERSION 必须等于最大的迁移版本，否则会出现"声明了版本却没有对应迁移"
    assert SCHEMA_VERSION == max(v for v, _ in MIGRATIONS)

    # 原有数据一条不少、值没变
    after = [dict(r) for r in conn.execute("SELECT date, metric, value FROM daily_metrics ORDER BY date, metric")]
    assert after == before
    assert conn.execute("SELECT COUNT(*) AS n FROM days").fetchone()["n"] == 1
    run = conn.execute("SELECT * FROM sync_runs").fetchone()
    assert run["status"] == "ok" and run["succeeded"] == 31
    assert run["warnings"] is None and run["fields"] is None  # 旧记录留空，不是 0


def test_upgrade_then_repeat_migrate_is_idempotent(tmp_path):
    """升级到最新后再跑一次不能报错，也不能重复加列。"""
    conn = _build_legacy_database(tmp_path / "legacy2.db", 1)
    conn.execute("INSERT INTO daily_metrics (date, metric, value) VALUES ('2026-10-01', 'steps', 1.0)")
    conn.commit()

    migrate(conn)
    columns_once = [r["name"] for r in conn.execute("PRAGMA table_info(sync_runs)")]
    migrate(conn)
    migrate(conn)
    columns_twice = [r["name"] for r in conn.execute("PRAGMA table_info(sync_runs)")]

    assert columns_twice == columns_once
    assert columns_once.count("warnings") == 1 and columns_once.count("fields") == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_metrics").fetchone()["n"] == 1


def test_total_active_minutes_is_derived_not_stored():
    """派生值只在一处算（metrics.total_active_minutes），且 `0` 与 `None` 必须分开。

    口径（@Cindy 定稿）：
    - light 缺失(None) → None：不知道轻活动有多少，任何数都是编的；
    - light = 0       → 0 + (mod or 0) + (vig or 0)：真的没有轻活动，合法；
    - light 有值      → light + (mod or 0) + (vig or 0)。

    为什么只对 light 严格：中/高缺失时 total = light，仍 > mvpa；而 **light 缺失时
    total 必然退化成等于 mvpa**，那是结构性重复（正是本字段要消除的问题），不是精度问题。
    """
    from health_by import metrics
    from health_by.models import ActivityDay

    # 派生值不落库：落库会在加字段那天给所有历史数据留永久空洞
    assert "total_active_minutes" not in ActivityDay.__dataclass_fields__

    # 唯一闸门：light 缺失绝不允许退化成等于 mvpa
    assert metrics.total_active_minutes(None, 20, 10) is None
    assert metrics.total_active_minutes(None, None, None) is None

    assert metrics.total_active_minutes(117, 17, 57) == 191
    assert metrics.total_active_minutes(29, None, None) == 29
    # light=0 时等于 mvpa 是合法的（确实没有轻活动），与"缺失"必须分开
    assert metrics.total_active_minutes(0, 20, 10) == 30
    assert metrics.total_active_minutes(0, None, None) == 0

    for light, moderate, vigorous in ((117, 17, 57), (29, None, None), (0, 20, 10), (0, None, None)):
        total = metrics.total_active_minutes(light, moderate, vigorous)
        mvpa = None if (moderate is None and vigorous is None) else (moderate or 0) + (vigorous or 0)
        assert total is None or mvpa is None or total >= mvpa
