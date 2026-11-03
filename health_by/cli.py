"""Click CLI：serve / sync / status / export / db。"""

from __future__ import annotations

from health_by.i18n import t, using_language

import json
import sys
from pathlib import Path

import click

from . import __version__, storage
from .config import load_config
from .coverage import raw_coverage
from .db import connect, migrate
from .engine import Engine
from .providers.base import ProviderAuthError
from .util import iso_range, iso_today


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
        ctx.obj = {"cfg": load_config(data_dir)}
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None


def _open(ctx: click.Context):
    cfg = ctx.obj["cfg"]
    conn = connect(cfg.db_path)
    migrate(conn)
    engine = Engine(cfg, conn)
    return cfg, conn, engine


@main.command(help=t('server.cli.serve.help.69704dbe2'))
@localized_option("--host", default=None, help=t('server.cli.serve.help.29f86549c'))
@localized_option("--port", default=None, type=int, help=t('server.cli.serve.help.fc39c6bd7'))
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """启动 Web 服务（首次运行会自动准备演示数据）。"""
    cfg = ctx.obj["cfg"]
    import uvicorn

    from .api.app import create_app

    app = create_app(cfg)
    click.echo(t('server.cli.serve.8155a3f15', p1=host or cfg.host, p2=port or cfg.port, p3=cfg.data_dir))
    uvicorn.run(app, host=host or cfg.host, port=port or cfg.port, log_level="info")


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
    """导出全部数据（归一化 + 原始留档）为 JSON。"""
    _, conn, engine = _open(ctx)
    try:
        payload = storage.export_all(conn)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        click.echo(t('server.cli.export.0f6e05494', out=out, p2=len(payload['days']), p3=len(payload['raw_days'])))
    finally:
        engine.close()
        conn.close()


@main.group(help=t('server.cli.db.help.a48930fb6'))
def db() -> None:
    """数据库维护：stats / check / vacuum。"""


def _db_size(path: Path) -> str:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            total += candidate.stat().st_size
    return f"{total / 1024 / 1024:.1f} MB"


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


if __name__ == "__main__":  # pragma: no cover
    main()
