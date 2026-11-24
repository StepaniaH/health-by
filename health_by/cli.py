"""Click CLI：serve / sync / status / export / db。"""

from __future__ import annotations

from health_by.i18n import t, using_language

import copy
from dataclasses import replace
from datetime import date as date_type, datetime, timezone
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import click

from . import __version__, storage
from .config import load_config
from .coverage import raw_coverage
from .db import backup_database, connect, migrate
from .engine import Engine
from .importers.fitbit_zip import import_fitbit_export
from .legacy import LEGACY_DB_NAME
from .providers import available_providers, get_provider
from .providers.base import ProviderAuthError, RawDay
from .util import iso_range, iso_today, utc_now_iso


class _LocalizedHelp:
    def __init__(self, *args, **kwargs):
        self._message_help = kwargs.get("help")
        super().__init__(*args, **kwargs)

    def format_help_text(self, ctx, formatter):
        original = self.help
        if self._message_help is not None:
            self.help = str(self._message_help)
        try:
            return super().format_help_text(ctx, formatter)
        finally:
            self.help = original

    def get_short_help_str(self, *args, **kwargs):
        original = self.help
        if self._message_help is not None:
            self.help = str(self._message_help)
        try:
            return super().get_short_help_str(*args, **kwargs)
        finally:
            self.help = original


class LocalizedCommand(_LocalizedHelp, click.Command):
    pass


class LocalizedGroup(_LocalizedHelp, click.Group):
    command_class = LocalizedCommand
    group_class = type


class LocalizedOption(click.Option):
    def __init__(self, *args, **kwargs):
        self._message_help = kwargs.get("help")
        super().__init__(*args, **kwargs)

    def get_help_record(self, ctx):
        original = self.help
        if self._message_help is not None:
            self.help = str(self._message_help)
        try:
            return super().get_help_record(ctx)
        finally:
            self.help = original


def localized_option(*args, **kwargs):
    return click.option(*args, cls=LocalizedOption, **kwargs)


def _select_cli_language(ctx, param, value):
    context = using_language(value)
    context.__enter__()
    ctx.call_on_close(lambda: context.__exit__(None, None, None))
    return value


@click.group(cls=LocalizedGroup, context_settings={"help_option_names": ["-h", "--help"]}, help=t('server.cli.main.help.7d67857f9'))
@localized_option("--lang", type=click.Choice(["zh", "en"]), envvar="HEALTH_BY_LANG", default="zh", is_eager=True, expose_value=False, callback=_select_cli_language)
@click.version_option(__version__, prog_name="health-by")
@localized_option(
    "--data",
    "data_dir",
    type=click.Path(path_type=Path),
    default=None,
    help=t('server.cli.main.help.a7fba029e'),
)
@click.pass_context
def main(ctx: click.Context, data_dir: Path | None) -> None:
    """health-by - 自托管健身数据服务（Web 界面 + CLI）。"""
    try:
        ctx.obj = {"cfg": load_config(data_dir, ensure=False)}
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None


def _open(ctx: click.Context):
    cfg = ctx.obj["cfg"]
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    migrate(conn)
    engine = Engine(cfg, conn)
    return cfg, conn, engine


def _open_readonly(ctx: click.Context):
    """供 `--dry-run` 只读打开已有数据库：绝不建目录、绝不创建新库、绝不触发迁移。

    若目录中只有旧名 `fitbit-by.db`，会以只读方式读取它并提示，避免预演结果与正式执行不一致；
    当目录中不存在非空 `-wal` 文件时启用 `immutable=1`，避免预演在目录里留下空的 `-wal`/`-shm` 副产物。
    """
    cfg = ctx.obj["cfg"]
    db_path = cfg.db_path
    if not db_path.exists():
        legacy_path = cfg.data_dir / LEGACY_DB_NAME
        if legacy_path.exists():
            db_path = legacy_path
            click.echo(
                t("server.cli.dry_run.legacy_db_notice", legacy=legacy_path.name, current=cfg.db_path.name),
                err=True,
            )
        else:
            return cfg, None
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    has_wal_sidecar = wal_path.exists() or shm_path.exists()
    query = "mode=ro" if has_wal_sidecar else "mode=ro&immutable=1"
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?{query}"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return cfg, conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


_SENSITIVE_QUERY_RE = re.compile(r"(/oauth/callback|/settings)\?([^\s\"']+)")


def redact_access_target(text: str) -> str:
    """脱敏访问日志中的 OAuth 回调或错误参数 query 串，防止 code/state 落盘。"""
    return _SENSITIVE_QUERY_RE.sub(r"\1?[redacted]", text)


class _RedactAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_access_target(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_access_target(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: (redact_access_target(value) if isinstance(value, str) else value)
                for key, value in record.args.items()
            }
        return True


@main.command(help=t('server.cli.serve.help.69704dbe2'))
@localized_option("--host", default=None, help=t('server.cli.serve.help.29f86549c'))
@localized_option("--port", default=None, type=int, help=t('server.cli.serve.help.fc39c6bd7'))
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """启动 Web 服务。"""
    cfg = ctx.obj["cfg"]
    effective_host = host or cfg.host
    effective_port = port if port is not None else cfg.port
    cfg = replace(cfg, host=effective_host, port=effective_port)
    cfg.ensure_dirs()
    import uvicorn
    import uvicorn.config

    from .api.app import create_app

    app = create_app(cfg)
    click.echo(t('server.cli.serve.8155a3f15', p1=cfg.host, p2=cfg.port, p3=cfg.data_dir))
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config.setdefault("filters", {})["redact_oauth"] = {"()": "health_by.cli._RedactAccessLogFilter"}
    access_handler = log_config.get("handlers", {}).get("access")
    if isinstance(access_handler, dict):
        access_handler["filters"] = [*access_handler.get("filters", []), "redact_oauth"]
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info", log_config=log_config)


@main.command("sync", help=t('server.cli.sync_cmd.help.c615d7fc4'))
@localized_option("--date", "date_value", default=None, help=t('server.cli.sync_cmd.help.2a15ef509'))
@localized_option("--days", default=1, show_default=True, type=int, help=t('server.cli.sync_cmd.help.e57d6401b'))
@click.pass_context
def sync_cmd(ctx: click.Context, date_value: str | None, days: int) -> None:
    """手动同步数据。"""
    _, conn, engine = _open(ctx)
    try:
        dates = [date_value] if date_value else iso_range(iso_today(), max(1, min(days, 90)))
        try:
            results = engine.sync_days(dates)
        except ProviderAuthError as exc:
            click.echo(t('server.cli.sync_cmd.3981f6db2', exc=exc), err=True)
            sys.exit(2)
        failed = 0
        for result in results:
            status = result["status"]
            if status == "ok":
                steps = result.get("steps")
                sleep = result.get("sleep_minutes")
                click.echo(t('server.cli.sync_cmd.aa3f9ebf4', p1=result['date'], p2=steps if steps is not None else '-', p3=sleep if sleep is not None else '-'))
            elif status == "empty":
                # 上游确实没有这天的数据，不是失败，不该影响退出码。
                click.echo(t('server.cli.sync_cmd.6b0eae777', p1=result['date']))
            elif status == "partial":
                stats = result.get("stats") or {}
                total, ok = stats.get("total"), stats.get("succeeded")
                missing = t('separator.list').join(result.get("failed_jobs") or []) or t('server.cli.sync_cmd.dcb4c2c2f')
                click.echo(t('server.cli.sync_cmd.0a72cbb21', p1=result['date'], ok=ok, total=total, missing=missing))
                failed += 1
            else:
                click.echo(t('server.cli.sync_cmd.4e49312b1', p1=result['date'], p2=result.get('error') or t('server.cli.sync_cmd.336e8460f')))
                failed += 1
        if failed:
            # 脚本用户靠退出码判断成败：有失败就必须非零，否则 cron/CI 永远以为成功。
            click.echo(t('server.cli.sync_cmd.7e132c539', failed=failed), err=True)
            sys.exit(1)
    finally:
        engine.close()
        conn.close()


@main.command(help=t('server.cli.status.help.cba9f102e'))
@click.pass_context
def status(ctx: click.Context) -> None:
    """显示数据源连接与数据存储状态。"""
    _, conn, engine = _open(ctx)
    try:
        auth = engine.auth_status()
        click.echo(t('server.cli.status.1529cf34b', p1=auth.get('display_name'), p2=auth.get('provider')))
        if auth.get("auth_kind") == "oauth":
            connected = auth.get("connected") and not auth.get("needs_reconnect")
            click.echo(t('server.cli.status.35d0f0ae4', p1=t('server.cli.status.5be0323e8') if connected else t('server.cli.status.2a7573e88') if auth.get('needs_reconnect') else t('server.cli.status.3d52574ce')))
        click.echo(t('server.cli.status.37598f268', p1=storage.count_days(conn), p2=storage.latest_day_date(conn) or '-'))
        coverage = raw_coverage(conn)
        click.echo(t('server.cli.status.d2002d7f4', p1=coverage['raw_days']))
        click.echo(t('server.cli.status.c32ceff36', p1=coverage['normalized_days']))
        click.echo(t('server.cli.status.1c780c242', p1=coverage['missing_count']))
        last = next(iter(storage.list_sync_runs(conn, limit=1)), None)
        if last:
            click.echo(t('server.cli.status.f129524a1', p1=last['finished_at'], p2=last['status'], p3=last['succeeded'], p4=last['total']))
    finally:
        engine.close()
        conn.close()


@main.command(help=t('server.cli.export.help.806d6cf57'))
@click.argument("out", type=click.Path(path_type=Path))
@click.pass_context
def export(ctx: click.Context, out: Path) -> None:
    """导出全部数据（归一化 + 原始留档）为流式 JSON（原子替换，0600）。"""
    cfg, conn, engine = _open(ctx)
    try:
        resolved_out = out.expanduser().resolve()
        db_targets = {
            Path(f"{cfg.db_path.resolve()}{suffix}")
            for suffix in ("", "-wal", "-shm")
        }
        if resolved_out in db_targets:
            raise click.ClickException(t('server.cli.export.refuse_db_target', out=out))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{out.name}.",
                suffix=".tmp",
                dir=str(out.parent),
            )
            tmp_path = Path(tmp_name)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    for chunk in storage.iter_export_json(conn):
                        fh.write(chunk)
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
                days_count = storage.count_days(conn)
                raw_count = int(conn.execute("SELECT COUNT(*) AS n FROM raw_days").fetchone()["n"])
            finally:
                if conn.in_transaction:
                    conn.rollback()
            os.replace(tmp_path, out)
            tmp_path = None
            try:
                os.chmod(out, 0o600)
            except OSError:
                pass
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        click.echo(t('server.cli.export.0f6e05494', out=out, p2=days_count, p3=raw_count))
    finally:
        engine.close()
        conn.close()


@main.group(help=t('server.cli.db.help.a48930fb6'))
def db() -> None:
    """数据库维护：stats / check / vacuum。"""


def _db_size(path: Path) -> str:
    return _fmt_bytes(_db_size_bytes(path))


def _db_size_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _fmt_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


@db.command("stats", help=t('server.cli.db_stats.help.3b43adc40'))
@click.pass_context
def db_stats(ctx: click.Context) -> None:
    """表行数、文件大小、日志模式。"""
    cfg, conn, engine = _open(ctx)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("days", "daily_metrics", "raw_days", "sync_runs")
        }
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        schema = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        click.echo(t('server.cli.db_stats.3680bb99f', p1=cfg.db_path))
        click.echo(t('server.cli.db_stats.673e141b6', p1=_db_size(cfg.db_path), journal=journal, p3=schema['value'] if schema else '?'))
        click.echo(
            t('server.cli.db_stats.f4ebc1822', p1=counts['days'], p2=counts['daily_metrics'], p3=counts['raw_days'], p4=counts['sync_runs'])
        )
    finally:
        engine.close()
        conn.close()


@db.command("check", help=t('server.cli.db_check.help.cb8f2e018'))
@click.pass_context
def db_check(ctx: click.Context) -> None:
    """完整性检查（integrity_check + foreign_key_check）。"""
    _, conn, engine = _open(ctx)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        click.echo(f"integrity_check：{integrity}")
        click.echo(t('server.cli.db_check.068cce01c', p1=t('server.cli.db_check.1e9f2561b') if not violations else t('server.cli.db_check.464ac7f39', p1=len(violations))))
        if violations:
            for row in violations[:20]:
                click.echo(f"  {tuple(row)}")
            sys.exit(1)
    finally:
        engine.close()
        conn.close()


@db.command("vacuum", help=t('server.cli.db_vacuum.help.b0292f1df'))
@click.pass_context
def db_vacuum(ctx: click.Context) -> None:
    """压缩数据库文件（VACUUM）。"""
    cfg, conn, engine = _open(ctx)
    try:
        before = _db_size(cfg.db_path)
        conn.execute("VACUUM")
        click.echo(t('server.cli.db_vacuum.a75166e21', before=before, p2=_db_size(cfg.db_path)))
    finally:
        engine.close()
        conn.close()


@db.command("compress-raw", help=t('server.cli.db_compress_raw.help.summary'))
@localized_option("--backup/--no-backup", default=True, help=t('server.cli.db_compress_raw.help.backup'))
@localized_option("--dry-run", is_flag=True, default=False, help=t('server.cli.db_compress_raw.help.dry_run'))
@localized_option("--vacuum/--no-vacuum", default=True, help=t('server.cli.db_compress_raw.help.vacuum'))
@localized_option("--batch-size", type=click.IntRange(min=1), default=100, show_default=True, help=t('server.cli.db_compress_raw.help.batch_size'))
@click.pass_context
def db_compress_raw(
    ctx: click.Context,
    backup: bool,
    dry_run: bool,
    vacuum: bool,
    batch_size: int,
) -> None:
    """将历史未压缩 raw_days 行分批压缩为 zlib BLOB，并按需创建备份与 VACUUM。"""
    if dry_run:
        cfg, conn = _open_readonly(ctx)
        engine = None
    else:
        cfg, conn, engine = _open(ctx)
    try:
        if conn is None or not _has_table(conn, "raw_days"):
            stats = {"total": 0, "converted": 0, "skipped": 0, "bytes_before": 0, "bytes_after": 0}
        else:
            db_before = _db_size(cfg.db_path) if not dry_run else ""
            if backup and not dry_run:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup_path = cfg.db_path.parent / f"{cfg.db_path.name}.pre-compress-{stamp}.bak"
                if backup_path.exists():
                    backup_path = cfg.db_path.parent / f"{cfg.db_path.name}.pre-compress-{stamp}-{os.getpid()}.bak"
                backup_database(conn, backup_path)
                click.echo(t('server.cli.db_compress_raw.backup_created', path=backup_path))

            stats = storage.compress_raw_days(conn, batch_size=batch_size, dry_run=dry_run)
            if vacuum and not dry_run and stats["converted"] > 0:
                conn.execute("VACUUM")
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.OperationalError:
                    pass

        before_str = _fmt_bytes(stats["bytes_before"])
        after_str = _fmt_bytes(stats["bytes_after"])
        if dry_run:
            click.echo(
                t(
                    'server.cli.db_compress_raw.dry_run_done',
                    total=stats["total"],
                    converted=stats["converted"],
                    skipped=stats["skipped"],
                    before=before_str,
                    after=after_str,
                )
            )
        else:
            click.echo(
                t(
                    'server.cli.db_compress_raw.done',
                    total=stats["total"],
                    converted=stats["converted"],
                    skipped=stats["skipped"],
                    before=before_str,
                    after=after_str,
                    db_before=db_before,
                    db_after=_db_size(cfg.db_path),
                )
            )
    finally:
        if engine is not None:
            engine.close()
        if conn is not None:
            conn.close()


def _parse_iso_date(value: str) -> str:
    try:
        return date_type.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise click.ClickException(t('server.cli.retranslate.invalid_date', value=value)) from exc


@main.command("retranslate", help=t('server.cli.retranslate.help.summary'))
@localized_option("--date", "target_date", default=None, help=t('server.cli.retranslate.help.date'))
@localized_option("--start", "start_date", default=None, help=t('server.cli.retranslate.help.start'))
@localized_option("--end", "end_date", default=None, help=t('server.cli.retranslate.help.end'))
@localized_option("--all", "retranslate_all", is_flag=True, default=False, help=t('server.cli.retranslate.help.all'))
@localized_option("--dry-run", is_flag=True, default=False, help=t('server.cli.retranslate.help.dry_run'))
@click.pass_context
def retranslate_cmd(
    ctx: click.Context,
    target_date: str | None,
    start_date: str | None,
    end_date: str | None,
    retranslate_all: bool,
    dry_run: bool,
) -> None:
    """使用本地 raw_days 离线重译日快照、指标与测量日期索引（无网络调用）。"""
    has_range = bool(start_date or end_date)
    scopes = sum((bool(retranslate_all), bool(target_date), has_range))
    if scopes == 0:
        raise click.ClickException(t('server.cli.retranslate.need_scope'))
    if scopes > 1:
        raise click.ClickException(t('server.cli.retranslate.ambiguous_scope'))

    if dry_run:
        _, conn = _open_readonly(ctx)
        engine = None
    else:
        _, conn, engine = _open(ctx)
    try:
        if conn is None or not _has_table(conn, "raw_days"):
            click.echo(t('server.cli.retranslate.empty'))
            return
        if retranslate_all:
            dates = [row[0] for row in conn.execute("SELECT date FROM raw_days ORDER BY date")]
        elif target_date:
            normalized = _parse_iso_date(target_date)
            dates = [
                row[0]
                for row in conn.execute("SELECT date FROM raw_days WHERE date = ? ORDER BY date", (normalized,))
            ]
        else:
            start_iso = _parse_iso_date(start_date or end_date or "")
            end_iso = _parse_iso_date(end_date or start_date or "")
            if start_iso > end_iso:
                start_iso, end_iso = end_iso, start_iso
            dates = [
                row[0]
                for row in conn.execute(
                    "SELECT date FROM raw_days WHERE date >= ? AND date <= ? ORDER BY date",
                    (start_iso, end_iso),
                )
            ]

        if not dates:
            click.echo(t('server.cli.retranslate.empty'))
            return

        has_days_table = _has_table(conn, "days")
        providers = set(available_providers())
        updated = 0
        skipped = 0
        failed = 0
        # 逐日读取与重译，避免 `--all` 一次性把全部压缩载荷拉入内存。
        for row_date in dates:
            row = conn.execute(
                "SELECT date, source, fetched_at, payload FROM raw_days WHERE date = ?",
                (row_date,),
            ).fetchone()
            if row is None:
                continue
            source = str(row["source"])
            if source not in providers:
                skipped += 1
                click.echo(t('server.cli.retranslate.skipped_source', date=row_date, source=source))
                continue
            try:
                raw_payload = storage.decode_raw_payload(row["payload"])
                existing_cov: dict = {}
                if has_days_table:
                    existing_day = storage.get_day(conn, row_date)
                    if isinstance(existing_day, dict) and isinstance(existing_day.get("coverage"), dict):
                        existing_cov = existing_day["coverage"]
                errors = list(existing_cov.get("errors") or [])
                if existing_cov.get("endpoint_count"):
                    stats = {
                        "total": int(existing_cov["endpoint_count"]),
                        "succeeded": int(existing_cov.get("success_count") or 0),
                    }
                elif isinstance(raw_payload, dict):
                    key_count = len(raw_payload)
                    stats = {"total": key_count + len(errors), "succeeded": key_count}
                else:
                    stats = {}
                raw = RawDay(
                    date=row_date,
                    source=source,
                    generated_at=str(row["fetched_at"] or utc_now_iso()),
                    payload=raw_payload,
                    errors=errors,
                    stats=stats,
                )
                day = get_provider(source).translate(raw)
                if not dry_run:
                    storage.replace_normalized_day(conn, day, raw_payload=raw_payload)
                updated += 1
            except Exception as exc:
                failed += 1
                click.echo(t('server.cli.retranslate.day_failed', date=row_date, error=exc), err=True)

        msg_key = 'server.cli.retranslate.dry_summary' if dry_run else 'server.cli.retranslate.summary'
        click.echo(t(msg_key, total=len(dates), updated=updated, skipped=skipped, failed=failed))
        if failed:
            sys.exit(1)
    finally:
        if engine is not None:
            engine.close()
        if conn is not None:
            conn.close()


@main.command("import-fitbit", help=t('server.cli.import_fitbit.help.summary'))
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@localized_option("--overwrite", is_flag=True, default=False, help=t('server.cli.import_fitbit.help.overwrite'))
@localized_option("--dry-run", is_flag=True, default=False, help=t('server.cli.import_fitbit.help.dry_run'))
@localized_option("--weight-unit", type=click.Choice(["lb", "kg"]), default="lb", show_default=True, help=t('server.cli.import_fitbit.help.weight_unit'))
@click.pass_context
def import_fitbit_cmd(
    ctx: click.Context,
    path: Path,
    overwrite: bool,
    dry_run: bool,
    weight_unit: str,
) -> None:
    """从本地 Fitbit / Google Takeout ZIP 或解压目录离线导入历史数据。"""
    if dry_run:
        _, conn = _open_readonly(ctx)
        engine = None
    else:
        _, conn, engine = _open(ctx)
    try:
        try:
            stats = import_fitbit_export(
                conn,
                path,
                overwrite=overwrite,
                dry_run=dry_run,
                weight_unit=weight_unit,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        key = 'server.cli.import_fitbit.dry_done' if dry_run else 'server.cli.import_fitbit.done'
        click.echo(
            t(
                key,
                files=stats["files_parsed"],
                days=stats["days_found"],
                imported=stats["imported"],
                skipped=stats["skipped_existing"],
            )
        )
        if stats.get("files_failed", 0) > 0:
            failed_files = list(stats.get("failed_files") or [])
            sample = ", ".join(failed_files[:5])
            if len(failed_files) > 5:
                sample = f"{sample} (+{len(failed_files) - 5})"
            click.echo(
                t("server.cli.import_fitbit.failed_files", count=stats["files_failed"], files=sample),
                err=True,
            )
            sys.exit(1)
    finally:
        if engine is not None:
            engine.close()
        if conn is not None:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    main()
