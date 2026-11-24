"""SQLite（WAL）连接与版本化迁移。

Schema 变更约定：向 MIGRATIONS 追加新版本块 + SCHEMA_VERSION 加一；
禁止破坏性修改已发布字段（参照 pic-shelf 同款约定）。
"""

from __future__ import annotations

from health_by.i18n import t

import logging
import os
import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 9

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credentials (
            provider   TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS days (
            date         TEXT PRIMARY KEY,
            source       TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            data         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date   TEXT NOT NULL,
            metric TEXT NOT NULL,
            value  REAL NOT NULL,
            PRIMARY KEY (date, metric)
        );
        CREATE TABLE IF NOT EXISTS raw_days (
            date       TEXT PRIMARY KEY,
            source     TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider    TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            date        TEXT,
            total       INTEGER,
            succeeded   INTEGER,
            status      TEXT,
            errors      TEXT
        );
        CREATE TABLE IF NOT EXISTS oauth_states (
            state      TEXT PRIMARY KEY,
            verifier   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_daily_metrics_metric ON daily_metrics (metric, date);
        """,
    ),
    (
        2,
        """
        -- 解析层/上游 schema 漂移的警告：同步"成功"但字段数塌了，不能只记在 errors 里
        -- （errors 表达的是请求失败，这里是请求成功但读不出东西，性质不同）。
        ALTER TABLE sync_runs ADD COLUMN warnings TEXT;
        """,
    ),
    (
        3,
        """
        -- 本次同步实际解析出的指标条数。漂移检测的基线必须取"上一次解析出多少"，
        -- 不能取 daily_metrics 的累积行数：那个只增不减（合并语义不删陈旧行），
        -- 会把警告永久重复下去，而且文案会说谎。旧行为 NULL，检测时跳过。
        ALTER TABLE sync_runs ADD COLUMN fields INTEGER;
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS ai_analyses (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            date                TEXT NOT NULL,
            model               TEXT NOT NULL,
            prompt_version      TEXT NOT NULL,
            content             TEXT,
            raw_content         TEXT,
            validation_state    TEXT,
            validation_json     TEXT,
            usage_json          TEXT NOT NULL DEFAULT '{}',
            created_at          TEXT NOT NULL,
            base_url            TEXT NOT NULL,
            status              TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_cache ON ai_analyses (date, model, prompt_version, base_url, status, id);
        CREATE TABLE IF NOT EXISTS ai_egress (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id         INTEGER NOT NULL REFERENCES ai_analyses(id) ON DELETE CASCADE,
            attempt             INTEGER NOT NULL CHECK (attempt IN (1, 2)),
            payload_keys_json   TEXT NOT NULL,
            payload_keys_hash   TEXT NOT NULL,
            payload_hash        TEXT NOT NULL,
            payload_shapes_json TEXT NOT NULL,
            request_hash        TEXT NOT NULL,
            assistant_echo_json TEXT,
            usage_json          TEXT NOT NULL DEFAULT '{}',
            status              TEXT NOT NULL,
            sent_at             TEXT NOT NULL,
            UNIQUE(analysis_id, attempt)
        );
        """,
    ),
    (
        5,
        """
        ALTER TABLE ai_egress ADD COLUMN session_id TEXT;
        ALTER TABLE ai_egress ADD COLUMN user_agent TEXT;
        """,
    ),
    (
        6,
        """
        ALTER TABLE ai_analyses ADD COLUMN start_date TEXT;
        ALTER TABLE ai_analyses ADD COLUMN end_date TEXT;
        UPDATE ai_analyses SET start_date = date, end_date = date;
        CREATE INDEX IF NOT EXISTS idx_ai_window_cache ON ai_analyses
            (start_date, end_date, model, prompt_version, base_url, status, id);
        """,
    ),
    (
        7,
        """
        ALTER TABLE ai_analyses ADD COLUMN payload_schema_version TEXT NOT NULL DEFAULT 'daily-v1';
        ALTER TABLE ai_egress ADD COLUMN payload_schema_version TEXT NOT NULL DEFAULT 'daily-v1';
        UPDATE ai_analyses SET payload_schema_version = 'window-v1' WHERE start_date != end_date;
        UPDATE ai_egress SET payload_schema_version =
            (SELECT payload_schema_version FROM ai_analyses WHERE id = ai_egress.analysis_id);
        CREATE INDEX IF NOT EXISTS idx_ai_schema_cache ON ai_analyses
            (start_date, end_date, model, prompt_version, payload_schema_version, base_url, status, id);
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS raw_measure_dates (
            raw_date     TEXT NOT NULL,
            measure_date TEXT NOT NULL,
            PRIMARY KEY (raw_date, measure_date)
        );
        CREATE INDEX IF NOT EXISTS idx_raw_measure_dates ON raw_measure_dates (measure_date);
        """,
    ),
    (
        9,
        """
        -- v0.0.3 removed the built-in demo provider; migrate persisted v0.0.2 settings cleanly.
        UPDATE settings SET value = '"google-health"' WHERE key = 'provider' AND value = '"demo"';
        """,
    ),

]


def connect(db_path: str | Path) -> sqlite3.Connection:
    path_str = str(db_path)
    is_new = path_str != ":memory:" and not Path(db_path).exists()
    conn = sqlite3.connect(path_str, check_same_thread=False)
    if is_new:
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
        logging.getLogger(__name__).warning(t('server.db.connect.913328f65'), Path(db_path).resolve())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _split_sql_statements(sql: str) -> list[str]:
    """将多条 SQL 语句拆分为单条可执行语句，避免 executescript 隐式提交破坏事务原子性。"""
    statements: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        candidate = "\n".join(buffer)
        if sqlite3.complete_statement(candidate):
            statements.append(candidate.strip())
            buffer.clear()
    remainder = "\n".join(buffer).strip()
    if remainder:
        statements.append(remainder)
    return statements


_ALTER_ADD_COLUMN_RE = re.compile(
    r"^ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"].lower() == column.lower() for row in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        statements = _split_sql_statements(sql)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row_in_tx = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            current = int(row_in_tx["value"]) if row_in_tx else 0
            if version <= current:
                conn.rollback()
                continue
            for stmt in statements:
                # 兼容旧版非事务迁移在中途中断后遗留的半迁移列，避免 duplicate column name 阻断恢复。
                alter_match = _ALTER_ADD_COLUMN_RE.match(stmt)
                if alter_match and _column_exists(conn, alter_match.group(1), alter_match.group(2)):
                    continue
                conn.execute(stmt)
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(version),),
            )
            conn.commit()
            current = version
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
    if current < SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise


def backup_database(conn: sqlite3.Connection, dest: Path) -> Path:
    """使用 sqlite3 online backup API 创建一致备份，并将权限设为 0600。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest_conn = sqlite3.connect(str(dest))
    try:
        conn.backup(dest_conn)
    finally:
        dest_conn.close()
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest
