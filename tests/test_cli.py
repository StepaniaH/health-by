"""CLI 测试（Click CliRunner，合成测试数据源，完全离线）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from health_by.cli import main


def run(data_dir, *args):
    runner = CliRunner()
    return runner.invoke(main, ["--data", str(data_dir), *args])


def test_version():
    from health_by import __version__

    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_sync_specific_date(tmp_path, synthetic_cfg):
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert result.exit_code == 0, result.output
    assert "ok 2026-06-15" in result.output

    result = run(data_dir, "status")
    assert "最新日期:2026-06-15" in result.output


def test_export_writes_json(tmp_path, synthetic_cfg):
    data_dir = tmp_path / "data"
    run(data_dir, "sync", "--date", "2026-06-15")
    out = tmp_path / "export.json"
    result = run(data_dir, "export", str(out))
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert "2026-06-15" in payload["days"]
    assert payload["days"]["2026-06-15"]["activity"]["steps"] is not None


def test_help_lists_commands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("serve", "sync", "status", "export", "db"):
        assert command in result.output


def _patch_provider(monkeypatch, factory, data_dir=None):
    """把一个合成数据源注册成 `synthetic`，并把它设为当前数据源。

    产品里没有内置演示数据；这份数据只存在于测试里。
    """
    from health_by import db as db_module, storage
    from health_by.config import load_config
    from health_by.providers import PROVIDERS, ensure_builtin_providers

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", factory())
    if data_dir is not None:
        monkeypatch.setenv("HEALTH_BY_DATA", str(data_dir))
        conn = db_module.connect(load_config().db_path)
        db_module.migrate(conn)
        storage.set_setting(conn, "provider", "synthetic")
        conn.close()


def test_sync_exit_code_is_truthful_when_a_day_errors(tmp_path, monkeypatch):
    """脚本用户靠退出码判断成败：有的一天彻底失败时必须非零。"""
    from health_by.providers.base import ProviderError
    from devtools.gen_demo_data import SyntheticProvider

    class AlwaysFails(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raise ProviderError("上游 500")

    data_dir = tmp_path / "data"
    _patch_provider(monkeypatch, AlwaysFails, data_dir)
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert "x " in result.output
    assert result.exit_code == 1, f"有一天失败却返回 {result.exit_code}：{result.output}"


def test_sync_partial_day_is_labelled_not_reported_as_plain_failure(tmp_path, monkeypatch):
    """部分成功既不是 x （看起来全挂了），也不能算成功（数据不全）。

    这一条同时守住一个回归：`partial` 状态没有 `error` 字段，
    以前会打印成「x  日期 None」。
    """
    from devtools.gen_demo_data import SyntheticProvider

    base = SyntheticProvider()

    class PartlyBroken(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raw = base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            raw.errors = [{"key": "sleep", "message": "boom", "status": 500}]
            total = int(raw.stats.get("total") or 1)
            raw.stats = {"total": total, "succeeded": total - 1}
            return raw

    data_dir = tmp_path / "data"
    _patch_provider(monkeypatch, PartlyBroken, data_dir)
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert "2026-06-15" in result.output
    assert "None" not in result.output, f"不该打印 error=None：{result.output}"
    assert "部分" in result.output
    assert result.exit_code == 1


def test_sync_all_good_exits_zero(tmp_path, synthetic_cfg):
    """全部成功时退出码必须是 0，不能因为"保守"就一律非零。"""
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--days", "2")
    assert result.exit_code == 0, result.output
    assert result.output.count("ok ") == 2


def test_retired_demo_command_is_not_available(tmp_path, product_provider_defaults):
    result = run(tmp_path / "data", "demo", "--days", "30")
    assert result.exit_code == 2
    assert "No such command" in result.output



def test_cli_export_writes_0600_streaming_json_and_redacts_oauth_logs(tmp_path):
    from health_by.cli import redact_access_target

    assert redact_access_target("/oauth/callback?code=secret-auth-code&state=s1") == "/oauth/callback?[redacted]"
    assert redact_access_target("/settings?oauth=error&message=token_err") == "/settings?[redacted]"
    assert redact_access_target("/api/v1/summary?date=2026-10-01") == "/api/v1/summary?date=2026-10-01"

    data_dir = tmp_path / "data"
    out_file = tmp_path / "backup.json"
    result = run(data_dir, "export", str(out_file))
    assert result.exit_code == 0, result.output
    assert (out_file.stat().st_mode & 0o777) == 0o600
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert "days" in payload and "raw_days" in payload


def test_serve_passes_overridden_host_port_to_create_app(tmp_path, monkeypatch):
    from health_by.api.app import create_app as real_create_app

    captured = {}

    def fake_create_app(cfg):
        captured["cfg"] = cfg
        app = real_create_app(cfg)
        captured["default_redirect_uri"] = app.state.default_redirect_uri
        app.state.engine.shutdown()
        app.state.engine.close()
        app.state.conn.close()
        return app

    def fake_uvicorn_run(app, **kwargs):
        captured["uvicorn"] = kwargs

    monkeypatch.setattr("health_by.api.app.create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    data_dir = tmp_path / "serve-data"
    result = run(data_dir, "serve", "--host", "0.0.0.0", "--port", "41000")
    assert result.exit_code == 0, result.output
    assert captured["cfg"].host == "0.0.0.0"
    assert captured["cfg"].port == 41000
    assert captured["default_redirect_uri"] == "http://127.0.0.1:41000/oauth/callback"
    assert captured["uvicorn"]["host"] == "0.0.0.0"
    assert captured["uvicorn"]["port"] == 41000


def test_export_refuses_db_target_and_keeps_existing_file_on_error(tmp_path, monkeypatch, synthetic_cfg):
    from health_by import storage as storage_module

    data_dir = synthetic_cfg.data_dir
    res = run(data_dir, "sync", "--date", "2026-10-15")
    assert res.exit_code == 0, res.output
    db_file = data_dir / "health-by.db"
    before_bytes = db_file.read_bytes()

    refused = run(data_dir, "export", str(db_file))
    assert refused.exit_code != 0
    assert db_file.read_bytes() == before_bytes

    out_file = tmp_path / "existing.json"
    out_file.write_text('{"keep": true}', encoding="utf-8")

    def boom_iter(_conn):
        yield '{"partial": '
        raise RuntimeError("disk full")

    monkeypatch.setattr(storage_module, "iter_export_json", boom_iter)
    failed = run(data_dir, "export", str(out_file))
    assert failed.exit_code != 0
    assert out_file.read_text(encoding="utf-8") == '{"keep": true}'
    assert not list(tmp_path.glob(".existing.json.*.tmp"))


def test_dry_run_commands_do_not_create_db_or_dir(tmp_path, product_provider_defaults):
    empty_dir = tmp_path / "never-created"
    r1 = run(empty_dir, "db", "compress-raw", "--dry-run")
    assert r1.exit_code == 0, r1.output
    assert not empty_dir.exists()

    r2 = run(empty_dir, "retranslate", "--all", "--dry-run")
    assert r2.exit_code == 0, r2.output
    assert not empty_dir.exists()


def test_dry_run_reads_legacy_db_and_leaves_no_wal_shm_sidecars(tmp_path):
    """只有旧名 fitbit-by.db 时 --dry-run 能准确统计且不迁移；普通库预演不残留 -wal/-shm。"""
    import sqlite3
    from health_by import db as db_module

    # 1. 仅含旧名 fitbit-by.db 且有 1 条未压缩 raw_days
    legacy_dir = tmp_path / "legacy-only"
    legacy_dir.mkdir()
    legacy_db = legacy_dir / "fitbit-by.db"
    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    db_module.migrate(conn)
    conn.execute(
        "INSERT INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        ("2026-06-01", "google-health", "2026-06-01T00:00:00Z", '{"stepsDaily": {"steps": 123}}'),
    )
    conn.commit()
    conn.close()

    res = run(legacy_dir, "--lang", "en", "db", "compress-raw", "--dry-run")
    assert res.exit_code == 0, res.output
    assert "1 to convert" in res.output
    assert not (legacy_dir / "health-by.db").exists()
    assert not (legacy_dir / "fitbit-by.db-wal").exists()
    assert not (legacy_dir / "fitbit-by.db-shm").exists()

    # 2. 正常 WAL 库关闭并完成 checkpoint 后，--dry-run 不产生新的 -wal/-shm 文件
    wal_dir = tmp_path / "wal-clean"
    wal_dir.mkdir()
    wal_db = wal_dir / "health-by.db"
    conn2 = db_module.connect(wal_db)
    db_module.migrate(conn2)
    conn2.execute(
        "INSERT INTO raw_days (date, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
        ("2026-06-02", "google-health", "2026-06-02T00:00:00Z", '{"stepsDaily": {"steps": 456}}'),
    )
    conn2.commit()
    conn2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn2.close()
    assert not (wal_dir / "health-by.db-wal").exists()
    assert not (wal_dir / "health-by.db-shm").exists()

    res2 = run(wal_dir, "--lang", "en", "db", "compress-raw", "--dry-run")
    assert res2.exit_code == 0, res2.output
    assert "1 to convert" in res2.output
    assert not (wal_dir / "health-by.db-wal").exists()
    assert not (wal_dir / "health-by.db-shm").exists()
