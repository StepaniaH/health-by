"""共享测试夹具。"""

from __future__ import annotations

import pytest

from health_by import db as db_module, storage
from health_by.config import load_config

_PRODUCT_PROVIDER = storage.DEFAULT_SETTINGS["provider"]


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_BY_DATA", str(tmp_path / "data"))
    cfg = load_config()
    return cfg


@pytest.fixture
def synthetic_cfg(tmp_cfg, synthetic_provider):
    """Configure a test source in the temporary database before opening an engine."""
    connection = db_module.connect(tmp_cfg.db_path)
    db_module.migrate(connection)
    storage.set_setting(connection, "provider", synthetic_provider)
    connection.close()
    return tmp_cfg


@pytest.fixture
def conn(synthetic_cfg):
    connection = db_module.connect(synthetic_cfg.db_path)
    db_module.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def synthetic_provider(monkeypatch):
    """把合成数据源注册进 PROVIDERS，供需要"有数据"的用例使用。

    产品里没有演示数据这个概念；这份数据只在测试里存在。
    """
    from health_by.providers import PROVIDERS, ensure_builtin_providers
    from devtools.gen_demo_data import SyntheticProvider

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SyntheticProvider())
    monkeypatch.setitem(storage.DEFAULT_SETTINGS, "provider", "synthetic")
    return "synthetic"


@pytest.fixture
def product_provider_defaults(synthetic_provider, monkeypatch):
    """Restore the actual product default and remove the test-only registry entry."""
    from health_by.providers import PROVIDERS

    monkeypatch.setitem(storage.DEFAULT_SETTINGS, "provider", _PRODUCT_PROVIDER)
    monkeypatch.delitem(PROVIDERS, "synthetic")
    return _PRODUCT_PROVIDER
