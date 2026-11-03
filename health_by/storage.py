"""SQLite 读写：归一化日数据、指标行、原始留档、同步记录、设置。"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .models import DaySnapshot, metric_values
from .providers.base import RawDay
from .util import shift_iso, utc_now_iso

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "google-health",
    "goals": {"steps": 8000, "sleep_minutes": 480},
    "sync_interval_minutes": 360,
    "ai_enabled": False,
    "ai_base_url": "http://127.0.0.1:11434/v1",
    "ai_model": "qwen2.5:3b",
}


# ---------- settings ----------


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            stored[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            stored[row["key"]] = row["value"]
    return {**DEFAULT_SETTINGS, **stored}


def save_settings(conn: sqlite3.Connection, patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        set_setting(conn, key, value)


# ---------- credentials（密文由调用方提供）----------


def save_credentials_raw(conn: sqlite3.Connection, provider: str, encrypted: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO credentials (provider, data, updated_at) VALUES (?, ?, ?)",
        (provider, encrypted, utc_now_iso()),
    )
    conn.commit()


def load_credentials_raw(conn: sqlite3.Connection, provider: str) -> str | None:
    row = conn.execute("SELECT data FROM credentials WHERE provider = ?", (provider,)).fetchone()
    return row["data"] if row else None


def delete_credentials(conn: sqlite3.Connection, provider: str) -> None:
    conn.execute("DELETE FROM credentials WHERE provider = ?", (provider,))
    conn.commit()


# ---------- days / metrics / raw ----------


def merge_day_dict(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """把新的日快照合并进已存的日快照，**只补不抹**。

    同步可能部分失败：某个数据源的请求挂了，读到的就是 None（或空集合），
    这跟「那天确实没有数据」在载荷里长得一模一样。所以这里采取保守策略——
    None 和空集合都视为「本次没拿到」，保留已存的旧值；只有真正拿到的新值才覆盖。

    代价是：如果上游确实删掉了某天的数据，本地不会跟着清空。对健康档案来说，
    宁可留旧值也不要因为一次网络抖动把历史抹掉；真要重算有 raw_days 可回放。
    """
    merged = dict(existing)
    for key, value in incoming.items():
        old = merged.get(key)
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(old, dict):
            merged[key] = merge_day_dict(old, value)
            continue
        if isinstance(value, (list, tuple)) and not value and old:
            continue
        merged[key] = value
    return merged


def json_safe(value: Any) -> Any:
    """把 NaN / ±Infinity 换成 None，保证产物是合法 JSON。

    `json.dumps` 默认 `allow_nan=True`，会把非有限浮点数写成裸的 `NaN` / `Infinity`
    —— Python 的 `json.loads` 能读回来，但浏览器的 `JSON.parse` 会直接报错。
    既然这份数据要经 HTTP 交给前端，就不能让它带这些东西出去。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def store_day(conn: sqlite3.Connection, day: DaySnapshot, raw: RawDay) -> None:
    """写入归一化日数据 + 指标行 + 原始留档（单事务）。

    同源重同步按字段合并，避免部分失败抹掉已有字段；来源变化时替换该日
    快照及指标，防止两个来源的字段混在一起。
    """
    with conn:
        incoming = day.to_dict()
        row = conn.execute("SELECT source, data FROM days WHERE date = ?", (day.date,)).fetchone()
        source_changed = row is not None and row["source"] != day.source
        merged = incoming
        if row is not None and not source_changed:
            try:
                merged = merge_day_dict(json.loads(row["data"]), incoming)
            except json.JSONDecodeError:
                merged = incoming
        conn.execute(
            "INSERT OR REPLACE INTO days (date, source, generated_at, data) VALUES (?, ?, ?, ?)",
            (day.date, day.source, day.generated_at, json.dumps(json_safe(merged), ensure_ascii=False)),
        )
        # 同源保留缺失指标；跨源必须清除旧指标，不能把旧来源的值当成新来源的数据。
        if source_changed:
            conn.execute("DELETE FROM daily_metrics WHERE date = ?", (day.date,))
        for metric, value in metric_values(day).items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_metrics (date, metric, value) VALUES (?, ?, ?)",
                (day.date, metric, value),
            )
        existing_raw = conn.execute("SELECT source, payload FROM raw_days WHERE date = ?", (day.date,)).fetchone()
        payload = raw.payload
        if existing_raw is not None and existing_raw["source"] == raw.source and not payload:
            payload = json.loads(existing_raw["payload"])  # 本次没抓到原始载荷时保留旧的
        conn.execute(
            "INSERT OR REPLACE INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
            (day.date, raw.source, raw.generated_at, json.dumps(payload, ensure_ascii=False)),
        )
        # 测量日期在写入时就地索引，读取覆盖度时不必再解析整份载荷。
        from .coverage import forget_raw_dates, measure_dates

        forget_raw_dates(conn, day.date)
        for measured in measure_dates(payload):
            conn.execute(
                "INSERT OR IGNORE INTO raw_measure_dates (raw_date, measure_date) VALUES (?, ?)",
                (day.date, measured),
            )


def get_day(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute("SELECT data FROM days WHERE date = ?", (date,)).fetchone()
    return json.loads(row["data"]) if row else None


def latest_day_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) AS d FROM days").fetchone()
    return row["d"] if row and row["d"] else None


def available_dates(conn: sqlite3.Connection) -> list[str]:
    return [row["date"] for row in conn.execute("SELECT date FROM days ORDER BY date")]


def last_field_count(conn: sqlite3.Connection, date: str) -> int | None:
    """该日期最近一次同步实际解析出的字段数（漂移检测的基线）。

    基线取每次同步实际解析出的字段数，不取 `daily_metrics` 的累积行数：
    合并语义下它只增不减，同一次漂移会被反复报告。
    老库（v3 之前）这一列是 NULL，返回 None 表示「没有可比基线」。
    """
    row = conn.execute(
        "SELECT fields FROM sync_runs WHERE date = ? AND fields IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (date,),
    ).fetchone()
    return int(row["fields"]) if row is not None else None


def count_days(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM days").fetchone()
    return int(row["n"])


def get_trends(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict[str, Any]]:
    """[end_date-days+1, end_date] 内的指标透视表（升序，仅包含已有数据的日期）。"""
    start = shift_iso(end_date, -(days - 1))
    rows = conn.execute(
        "SELECT date, metric, value FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
        (start, end_date),
    ).fetchall()
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_date.setdefault(row["date"], {"date": row["date"]})
        entry[row["metric"]] = row["value"]
    return [by_date[key] for key in sorted(by_date)]


def get_day_window(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict[str, Any]]:
    """[end_date-days+1, end_date] 内的完整日快照（升序，仅包含已有数据的日期）。"""
    start = shift_iso(end_date, -(days - 1))
    rows = conn.execute(
        "SELECT data FROM days WHERE date >= ? AND date <= ? ORDER BY date",
        (start, end_date),
    ).fetchall()
    return [json.loads(row["data"]) for row in rows]


# ---------- sync runs ----------


def record_sync_run(
    conn: sqlite3.Connection,
    *,
    provider: str,
    started_at: str,
    finished_at: str,
    date: str | None,
    total: int,
    succeeded: int,
    status: str,
    errors: list[dict] | None = None,
    warnings: list[dict] | None = None,
    fields: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sync_runs "
        "(provider, started_at, finished_at, date, total, succeeded, status, errors, warnings, fields) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            provider,
            started_at,
            finished_at,
            date,
            total,
            succeeded,
            status,
            json.dumps(errors, ensure_ascii=False) if errors else None,
            json.dumps(warnings, ensure_ascii=False) if warnings else None,
            fields,
        ),
    )
    conn.commit()


def list_sync_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["errors"] = json.loads(row["errors"]) if row["errors"] else []
        # 旧库（迁移到 v2 之前）没有 warnings 列，用 keys() 兜住（sqlite3.Row 没有 .get）
        entry["warnings"] = json.loads(row["warnings"]) if "warnings" in row.keys() and row["warnings"] else []
        runs.append(entry)
    return runs


def last_successful_sync(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT finished_at FROM sync_runs WHERE status IN ('ok', 'partial') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["finished_at"] if row else None


def last_synced_date(conn: sqlite3.Connection) -> str | None:
    """已处理到哪一天（含 `empty`）。

    与 `last_successful_sync` 的区别：那个看的是「上一次跑成功是什么时候」（时间），
    这个看的是「数据已经覆盖到哪一天」（日期）。增量补抓要的是后者。
    `empty`（同步成功但那天确实没有数据）也必须算走过，否则会一直重复回填。
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM sync_runs WHERE status IN ('ok', 'partial', 'empty') AND date IS NOT NULL"
    ).fetchone()
    return row["d"] if row and row["d"] else None


# ---------- OAuth state ----------


def save_oauth_state(conn: sqlite3.Connection, state: str, verifier: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oauth_states (state, verifier, created_at) VALUES (?, ?, ?)",
        (state, verifier, utc_now_iso()),
    )
    conn.commit()


def pop_oauth_state(conn: sqlite3.Connection, state: str) -> dict | None:
    row = conn.execute(
        "SELECT state, verifier, created_at FROM oauth_states WHERE state = ?", (state,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    conn.commit()
    return dict(row)


# ---------- 导出 / 清理 ----------


def export_all(conn: sqlite3.Connection) -> dict[str, Any]:
    days = {}
    for row in conn.execute("SELECT date, data FROM days ORDER BY date"):
        days[row["date"]] = json.loads(row["data"])
    raw_days = {}
    for row in conn.execute("SELECT date, payload FROM raw_days ORDER BY date"):
        raw_days[row["date"]] = json.loads(row["payload"])
    settings = load_settings(conn)
    ai_analyses = [dict(row) for row in conn.execute("SELECT * FROM ai_analyses ORDER BY id")]
    ai_egress = [dict(row) for row in conn.execute("SELECT * FROM ai_egress ORDER BY id")]
    return {
        "exported_at": utc_now_iso(),
        "settings": settings,
        "days": days,
        "raw_days": raw_days,
        "sync_runs": list_sync_runs(conn, limit=50),
        "ai_analyses": ai_analyses,
        "ai_egress": ai_egress,
    }


def delete_all_data(conn: sqlite3.Connection) -> None:
    """清空健康数据（保留连接凭据与设置）。"""
    with conn:
        for table in ("days", "daily_metrics", "raw_days", "sync_runs", "ai_egress", "ai_analyses"):
            conn.execute(f"DELETE FROM {table}")  # 表名为白名单常量，无注入面
