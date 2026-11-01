"""CLI 测试（Click CliRunner，演示数据源，完全离线）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from health_by.cli import main


def run(data_dir, *args):
    runner = CliRunner()
    return runner.invoke(main, ["--data", str(data_dir), *args])


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.output


def test_demo_then_status(tmp_path):
    data_dir = tmp_path / "data"
    result = run(data_dir, "demo", "--days", "3")
    assert result.exit_code == 0, result.output
    assert "已生成 3 天演示数据" in result.output

    result = run(data_dir, "status")
    assert result.exit_code == 0, result.output
    assert "存储天数:3" in result.output
    assert "原始覆盖 16 天" in result.output  # 三天演示载荷各含十四天滚动窗口。
    assert "归一化 3 天" in result.output
    assert "缺口 13 天" in result.output
    assert "演示数据" in result.output


def test_sync_specific_date(tmp_path):
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert result.exit_code == 0, result.output
    assert "ok 2026-06-15" in result.output

    result = run(data_dir, "status")
    assert "最新日期:2026-06-15" in result.output


def test_export_writes_json(tmp_path):
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
    for command in ("serve", "sync", "demo", "status", "export"):
        assert command in result.output


def _patch_provider(monkeypatch, factory):
    from health_by.providers import PROVIDERS, ensure_builtin_providers

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", factory())


def test_sync_exit_code_is_truthful_when_a_day_errors(tmp_path, monkeypatch):
    """脚本用户靠退出码判断成败：有的一天彻底失败时必须非零。"""
    from health_by.providers.base import ProviderError
    from health_by.providers.demo import DemoProvider

    class AlwaysFails(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raise ProviderError("上游 500")

    _patch_provider(monkeypatch, AlwaysFails)
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert "x " in result.output
    assert result.exit_code == 1, f"有一天失败却返回 {result.exit_code}：{result.output}"


def test_sync_partial_day_is_labelled_not_reported_as_plain_failure(tmp_path, monkeypatch):
    """部分成功既不是 x （看起来全挂了），也不能算成功（数据不全）。

    这一条同时守住一个回归：`partial` 状态没有 `error` 字段，
    以前会打印成「x  日期 None」。
    """
    from health_by.providers.demo import DemoProvider

    base = DemoProvider()

    class PartlyBroken(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raw = base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            raw.errors = [{"key": "sleep", "message": "boom", "status": 500}]
            total = int(raw.stats.get("total") or 1)
            raw.stats = {"total": total, "succeeded": total - 1}
            return raw

    _patch_provider(monkeypatch, PartlyBroken)
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--date", "2026-06-15")
    assert "2026-06-15" in result.output
    assert "None" not in result.output, f"不该打印 error=None：{result.output}"
    assert "部分" in result.output
    assert result.exit_code == 1


def test_sync_all_good_exits_zero(tmp_path):
    """全部成功时退出码必须是 0，不能因为"保守"就一律非零。"""
    data_dir = tmp_path / "data"
    result = run(data_dir, "sync", "--days", "2")
    assert result.exit_code == 0, result.output
    assert result.output.count("ok ") == 2
