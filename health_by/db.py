"""SQLite（WAL）连接与版本化迁移。

Schema 变更约定：向 MIGRATIONS 追加新版本块 + SCHEMA_VERSION 加一；
禁止破坏性修改已发布字段（参照 pic-shelf 同款约定）。
"""

from __future__ import annotations

from health_by.i18n import t

import sqlite3
import logging
from pathlib import Path

SCHEMA_VERSION = 7

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
        CREATE TABLE ai_analyses (
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
        CREATE INDEX idx_ai_cache ON ai_analyses (date, model, prompt_version, base_url, status, id);
        CREATE TABLE ai_egress (
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
        CREATE INDEX idx_ai_window_cache ON ai_analyses
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
        CREATE INDEX idx_ai_schema_cache ON ai_analyses
            (start_date, end_date, model, prompt_version, payload_schema_version, base_url, status, id);
        """,
    ),

]


def connect(db_path: str | Path) -> sqlite3.Connection:
    is_new = not Path(db_path).exists()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    if is_new:
        logging.getLogger(__name__).warning(t('server.db.connect.913328f65'), Path(db_path).resolve())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    for version, sql in MIGRATIONS:
        if version > current:
            conn.executescript(sql)
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()
