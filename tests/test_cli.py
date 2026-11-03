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
