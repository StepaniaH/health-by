"""运行配置：数据目录 / 绑定地址 / 出站请求限速参数。"""

from __future__ import annotations

from health_by.i18n import t

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .legacy import DB_NAME, LEGACY_DB_NAME, migrate_legacy_database

LOG = logging.getLogger(__name__)


def env_value(suffix: str, default=None):
    current, legacy = "HEALTH_BY_" + suffix, "FITBIT_BY_" + suffix
    new, old = os.environ.get(current), os.environ.get(legacy)
    new = new or None
    old = old or None
    if new is not None:
        if old is not None and old != new:
            LOG.warning(t('server.config.env_value.586eb98b9'), current, legacy, current)
        return new
    if old is not None:
        LOG.warning(t('server.config.env_value.96f601791'), legacy, current)
        return old
    return default


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 39441
DEFAULT_PACE_MS = 225  # Google Health 限速 <5 req/s；225ms 间距 ≈ 4.4 req/s


@dataclass
class Config:
    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    pace_ms: int = DEFAULT_PACE_MS

    _prepared: bool = field(default=False, init=False, repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_NAME

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def web_dir(self) -> Path:
        return Path(__file__).resolve().parent / "web"

    def ensure_dirs(self) -> None:
        if self._prepared:
            return
        self.data_dir = self.data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        LOG.warning(t('server.config.ensure_dirs.f27b0868f'), self.data_dir, self.db_path)
        migrate_legacy_database(self.data_dir)
        self._prepared = True


def load_config(
    data_dir: str | Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> Config:
    """数据目录解析：显式参数 > HEALTH_BY_DATA > ./data。"""
    resolved = data_dir if data_dir is not None else env_value("DATA", "./data")
    if data_dir is None and os.environ.get("FITBIT_BY_DATA") and not os.environ.get("HEALTH_BY_DATA"):
        legacy_dir = Path(resolved).expanduser().resolve()
        if not any((legacy_dir / name).exists() for name in (DB_NAME, LEGACY_DB_NAME)):
            raise ValueError(t('server.config.load_config.5b273d95d', legacy_dir=legacy_dir))
    pace_raw = env_value("PACE_MS")
    cfg = Config(
        data_dir=Path(resolved).expanduser().resolve(),
        host=host if host is not None else env_value("HOST", DEFAULT_HOST),
        port=int(port if port is not None else env_value("PORT", DEFAULT_PORT)),
        pace_ms=int(pace_raw) if pace_raw is not None else DEFAULT_PACE_MS,
    )
    cfg.pace_ms = max(0, cfg.pace_ms)
    cfg.ensure_dirs()
    return cfg
