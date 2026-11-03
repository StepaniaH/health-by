"""共享测试夹具。"""

from __future__ import annotations

import pytest

from health_by import db as db_module
from health_by.config import load_config


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_BY_DATA", str(tmp_path / "data"))
    cfg = load_config()
    return cfg


@pytest.fixture
def conn(tmp_cfg):
    connection = db_module.connect(tmp_cfg.db_path)
    db_module.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def synthetic_provider(tmp_cfg, monkeypatch):
    """把合成数据源注册进 PROVIDERS，供需要"有数据"的用例使用。

    产品里没有演示数据这个概念；这份数据只在测试里存在。
    """
    from health_by.providers import PROVIDERS, ensure_builtin_providers
    from devtools.gen_demo_data import DemoProvider

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", DemoProvider())
    return "synthetic"
