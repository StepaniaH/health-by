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
