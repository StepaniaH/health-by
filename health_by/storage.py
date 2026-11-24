"""SQLite 读写：归一化日数据、指标行、原始留档、同步记录、设置。"""

from __future__ import annotations

import base64
import json
import math
import sqlite3
import zlib
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

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
        if key == "coverage" and isinstance(value, dict):
            # coverage 描述的是本次同步各端点的采集结果，不是健康测量本身；
            # 当本次同步实际跑了端点（endpoint_count > 0）时，必须直接替换旧的 errors / counts，
            # 防止接口恢复后旧错误永久残留。
            if int(value.get("endpoint_count") or 0) > 0:
                merged[key] = dict(value)
            continue
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


def encode_raw_payload(payload: Any) -> bytes:
    """将原始载荷压缩为 zlib BLOB，大幅降低逐日窗口留档的磁盘占用。"""
    encoded = json.dumps(json_safe(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(encoded, level=6)


def is_compressed_raw_payload(stored: Any) -> bool:
    """判断 raw_days.payload 是否已经是有效的 zlib 压缩 JSON BLOB。"""
    if not isinstance(stored, (bytes, bytearray, memoryview)):
        return False
    try:
        decompressed = zlib.decompress(bytes(stored))
        json.loads(decompressed.decode("utf-8"))
        return True
    except (zlib.error, UnicodeDecodeError, ValueError):
        return False


def decode_raw_payload(stored: str | bytes | bytearray | memoryview) -> Any:
    """透明解码 zlib BLOB、zlib+b64 字符串或历史未压缩 JSON 文本。"""
    if isinstance(stored, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(stored)
        try:
            raw_bytes = zlib.decompress(raw_bytes)
        except zlib.error:
            pass
        return json.loads(raw_bytes.decode("utf-8"))
    if isinstance(stored, str):
        if stored.startswith("zlib+b64:"):
            raw_bytes = zlib.decompress(base64.b64decode(stored[9:]))
            return json.loads(raw_bytes.decode("utf-8"))
        return json.loads(stored)
    raise ValueError(f"Unsupported raw payload type: {type(stored).__name__}")


def compress_raw_days(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict[str, int]:
    """将历史未压缩 JSON 文本（或 zlib+b64）raw_days 行分批压缩为 zlib BLOB。"""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    dates = [row[0] for row in conn.execute("SELECT date FROM raw_days ORDER BY date")]
    total = 0
    converted = 0
    skipped = 0
    bytes_before = 0
    bytes_after = 0
    for offset in range(0, len(dates), batch_size):
        chunk = dates[offset : offset + batch_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT date, payload FROM raw_days WHERE date IN ({placeholders}) ORDER BY date",
            chunk,
        ).fetchall()
        updates: list[tuple[bytes, str]] = []
        for row in rows:
            stored = row["payload"]
            before_len = (
                len(bytes(stored))
                if isinstance(stored, (bytes, bytearray, memoryview))
                else len(str(stored).encode("utf-8"))
            )
            total += 1
            bytes_before += before_len
            if is_compressed_raw_payload(stored):
                skipped += 1
                bytes_after += before_len
                continue
            decoded = decode_raw_payload(stored)
            encoded = encode_raw_payload(decoded)
            converted += 1
            bytes_after += len(encoded)
            updates.append((encoded, row["date"]))
        if updates and not dry_run:
            with conn:
                conn.executemany("UPDATE raw_days SET payload = ? WHERE date = ?", updates)
    return {
        "total": total,
        "converted": converted,
        "skipped": skipped,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
    }


def replace_normalized_day(
    conn: sqlite3.Connection,
    day: DaySnapshot,
    *,
    raw_payload: Any | None = None,
) -> None:
    """用重译后的日快照覆盖归一化层（days / daily_metrics / raw_measure_dates），不改 raw_days。"""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO days (date, source, generated_at, data) VALUES (?, ?, ?, ?)",
            (day.date, day.source, day.generated_at, json.dumps(json_safe(day.to_dict()), ensure_ascii=False)),
        )
        conn.execute("DELETE FROM daily_metrics WHERE date = ?", (day.date,))
        for metric, value in metric_values(day).items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_metrics (date, metric, value) VALUES (?, ?, ?)",
                (day.date, metric, value),
            )
        if raw_payload is not None:
            from .coverage import record_raw_dates

            record_raw_dates(conn, day.date, raw_payload)


def store_day(
    conn: sqlite3.Connection,
    day: DaySnapshot,
    raw: RawDay,
    *,
    replace: bool = False,
    commit: bool = True,
) -> None:
    """写入归一化日数据 + 指标行 + 原始留档（默认单事务，也可并入调用方外层事务）。

    同源重同步默认按字段及原始顶层 job key 合并，避免部分失败抹掉已有字段；
    来源变化或显式指定 `replace=True`（如离线导入 `--overwrite`）时整日替换，
    防止旧来源或旧导入的残留字段混入。
    """
    with conn if commit else nullcontext():
        incoming = day.to_dict()
        row = conn.execute("SELECT source, data FROM days WHERE date = ?", (day.date,)).fetchone()
        source_changed = row is not None and row["source"] != day.source
        should_replace = replace or source_changed
        merged = incoming
        if row is not None and not should_replace:
            try:
                merged = merge_day_dict(json.loads(row["data"]), incoming)
            except json.JSONDecodeError:
                merged = incoming
        conn.execute(
            "INSERT OR REPLACE INTO days (date, source, generated_at, data) VALUES (?, ?, ?, ?)",
            (day.date, day.source, day.generated_at, json.dumps(json_safe(merged), ensure_ascii=False)),
        )
        # 同源保留缺失指标；跨源或显式覆盖必须清除旧指标，不能把旧值当成新数据。
        if should_replace:
            conn.execute("DELETE FROM daily_metrics WHERE date = ?", (day.date,))
        for metric, value in metric_values(day).items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_metrics (date, metric, value) VALUES (?, ?, ?)",
                (day.date, metric, value),
            )
        existing_raw = conn.execute("SELECT source, payload FROM raw_days WHERE date = ?", (day.date,)).fetchone()
        payload = raw.payload
        if not replace and existing_raw is not None and existing_raw["source"] == raw.source:
            try:
                existing_payload = decode_raw_payload(existing_raw["payload"])
            except (ValueError, zlib.error, UnicodeDecodeError):
                existing_payload = None
            if isinstance(existing_payload, dict) and isinstance(payload, dict):
                payload = {**existing_payload, **payload}
            elif not payload and existing_payload is not None:
                payload = existing_payload
        conn.execute(
            "INSERT OR REPLACE INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
            (day.date, raw.source, raw.generated_at, encode_raw_payload(payload)),
        )
        # 测量日期在写入时就地索引，读取覆盖度时不必再解析整份载荷。
        from .coverage import record_raw_dates

        record_raw_dates(conn, day.date, payload)


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
    commit: bool = True,
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
    if commit:
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


def last_completed_sync(conn: sqlite3.Connection) -> str | None:
    """最近一次完整执行的同步时间（含 `empty`，用于自动同步限频避免连续空日每分钟轮询）。"""
    row = conn.execute(
        "SELECT finished_at FROM sync_runs WHERE status IN ('ok', 'partial', 'empty') ORDER BY id DESC LIMIT 1"
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


def failed_dates_in_window(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[str]:
    """返回区间内最近一次同步状态仍为 `error` 的日期，避免中间失败日被更晚成功日跳过。"""
    rows = conn.execute(
        "SELECT r.date FROM sync_runs r "
        "JOIN (SELECT date, MAX(id) AS max_id FROM sync_runs "
        "      WHERE date IS NOT NULL AND date >= ? AND date <= ? GROUP BY date) latest "
        "  ON r.id = latest.max_id "
        "WHERE r.status = 'error' ORDER BY r.date",
        (start_date, end_date),
    ).fetchall()
    return [str(row["date"]) for row in rows]


# ---------- OAuth state ----------


def prune_expired_oauth_states(conn: sqlite3.Connection, ttl_seconds: int = 900) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).isoformat()
    cursor = conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (cutoff,))
    return int(cursor.rowcount or 0)


def save_oauth_state(conn: sqlite3.Connection, state: str, verifier: str) -> None:
    prune_expired_oauth_states(conn)
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
        raw_days[row["date"]] = decode_raw_payload(row["payload"])
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


def iter_export_json(conn: sqlite3.Connection) -> Iterator[str]:
    """按行流式序列化全量导出 JSON，避免一次性把全部 raw_days 载入内存。"""
    yield '{"exported_at":'
    yield json.dumps(utc_now_iso(), ensure_ascii=False)
    yield ',"settings":'
    yield json.dumps(load_settings(conn), ensure_ascii=False)
    yield ',"days":{'
    first = True
    for row in conn.execute("SELECT date, data FROM days ORDER BY date"):
        if not first:
            yield ","
        first = False
        yield json.dumps(row["date"], ensure_ascii=False)
        yield ":"
        yield json.dumps(json_safe(json.loads(row["data"])), ensure_ascii=False)
    yield '},"raw_days":{'
    first = True
    for row in conn.execute("SELECT date, payload FROM raw_days ORDER BY date"):
        if not first:
            yield ","
        first = False
        yield json.dumps(row["date"], ensure_ascii=False)
        yield ":"
        yield json.dumps(json_safe(decode_raw_payload(row["payload"])), ensure_ascii=False)
    yield '},"sync_runs":'
    yield json.dumps(list_sync_runs(conn, limit=50), ensure_ascii=False)
    yield ',"ai_analyses":['
    first = True
    for row in conn.execute("SELECT * FROM ai_analyses ORDER BY id"):
        if not first:
            yield ","
        first = False
        yield json.dumps(dict(row), ensure_ascii=False)
    yield '],"ai_egress":['
    first = True
    for row in conn.execute("SELECT * FROM ai_egress ORDER BY id"):
        if not first:
            yield ","
        first = False
        yield json.dumps(dict(row), ensure_ascii=False)
    yield "]}"


def get_clear_generation(conn: sqlite3.Connection) -> int:
    """读取持久化清空代际，用于跨进程同步在并发清空时中止写回。"""
    row = conn.execute("SELECT value FROM meta WHERE key = 'clear_generation'").fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def delete_all_data(conn: sqlite3.Connection) -> int:
    """清空健康数据（保留连接凭据与设置），启用安全擦除并递增持久化清空代际。"""
    conn.execute("PRAGMA secure_delete = ON")
    managed_tx = not conn.in_transaction
    if managed_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        new_gen = get_clear_generation(conn) + 1
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('clear_generation', ?)",
            (str(new_gen),),
        )
        for table in (
            "days",
            "daily_metrics",
            "raw_days",
            "raw_measure_dates",
            "sync_runs",
            "oauth_states",
            "ai_egress",
            "ai_analyses",
        ):
            conn.execute(f"DELETE FROM {table}")  # 表名为白名单常量，无注入面
        if managed_tx:
            conn.commit()
    except BaseException:
        if managed_tx and conn.in_transaction:
            conn.rollback()
        raise
    if not conn.in_transaction:
        conn.execute("VACUUM")
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
    return new_gen
