"""旧数据库的一次性离线迁移：先做 SQLite 一致性备份，再改名，不覆盖已有文件。"""

from __future__ import annotations

from health_by.i18n import t

import fcntl
from contextlib import closing
import logging
import os
import sqlite3
import time
from pathlib import Path

LOG = logging.getLogger(__name__)
LEGACY_DB_NAME = "fitbit-by.db"
DB_NAME = "health-by.db"


def migrate_legacy_database(data_dir: Path) -> None:
    old, new = data_dir / LEGACY_DB_NAME, data_dir / DB_NAME
    if not old.exists():
        return
    # 并发启动的新程序不能同时迁移；旧程序必须先停止，SQLite 锁也会阻止在线改名。
    with (data_dir / ".health-by-migration.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not old.exists():
            return
        if new.exists():
            LOG.warning(t('server.legacy.migrate_legacy_database.1f119a62c'), new, old)
            return
        backup = old.with_name(old.name + ".bak")
        suffix = 0
        while backup.exists():
            suffix += 1
            backup = old.with_name(old.name + f".bak.{suffix}")
        complete = False
        created = False
        try:
            started = time.monotonic()
            def progress(status, remaining, total):
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() - started > 60:
                    raise RuntimeError(t('server.legacy.progress.0ab452bea'))
            with closing(sqlite3.connect(old.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)) as source:
                # 先拒绝仍在使用的旧库，避免重启循环每次产生一份巨型备份。
                checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint[0] != 0:
                    raise RuntimeError(t('server.legacy.migrate_legacy_database.8bed8eb63'))
                if source.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise RuntimeError(t('server.legacy.migrate_legacy_database.31e6b97e8'))
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                created = True
                with closing(sqlite3.connect(backup)) as target:
                    source.backup(target, pages=256, progress=progress, sleep=0.01)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError(t('server.legacy.migrate_legacy_database.59bad7733'))
                complete = True
            os.chmod(backup, 0o600)
            with backup.open("rb") as saved:
                os.fsync(saved.fileno())
            directory_fd = os.open(data_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            # 同一目录硬链接+删除实现不覆盖目标的改名；若中途崩溃，两名均保留且数据不丢。
            os.link(old, new)
            old.unlink()
            directory_fd = os.open(data_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            LOG.warning(t('server.legacy.migrate_legacy_database.b5a48edad'), old, new, backup)
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            if created and not complete:
                backup.unlink(missing_ok=True)
            raise RuntimeError(t('server.legacy.migrate_legacy_database.bc93d628a', backup=backup, exc=exc)) from None
