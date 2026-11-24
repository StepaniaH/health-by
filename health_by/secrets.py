"""加密凭据存储：Fernet（密钥环境变量 HEALTH_BY_SECRET_KEY 或 data/secret.key，0600）。"""

from __future__ import annotations

from health_by.i18n import t

import json
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretError(Exception):
    """凭据加解密失败（密钥不匹配或数据损坏）。"""


class SecretStore:
    def __init__(self, key_path: str | Path, *, env_key: str | None = None):
        self.key_path = Path(key_path)
        self._env_key = env_key
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = self._load_or_create_key()
            try:
                self._fernet = Fernet(key)
            except (ValueError, TypeError) as exc:
                raise SecretError(t('server.secrets.invalid_key.4c38e12aa')) from exc
        return self._fernet

    def _validate_key(self, raw: bytes) -> bytes:
        try:
            Fernet(raw)
        except (ValueError, TypeError) as exc:
            raise SecretError(t('server.secrets.invalid_key.4c38e12aa')) from exc
        return raw

    def _load_or_create_key(self) -> bytes:
        external = self._env_key if self._env_key is not None else os.environ.get("HEALTH_BY_SECRET_KEY")
        if external is not None and external.strip():
            return self._validate_key(external.strip().encode("ascii", errors="strict"))
        if self.key_path.exists():
            self._tighten_permissions()
            return self._validate_key(self.key_path.read_bytes().strip())
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        tmp_path = self.key_path.parent / f".{self.key_path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp_path, self.key_path)
                return key
            except FileExistsError:  # 并发创建：保留已落盘密钥
                self._tighten_permissions()
                return self._validate_key(self.key_path.read_bytes().strip())
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _tighten_permissions(self) -> None:
        try:
            if self.key_path.stat().st_mode & 0o077:
                os.chmod(self.key_path, 0o600)
        except OSError:
            pass  # 权限收紧尽力而为；读取不受影响

    def encrypt_json(self, value: object) -> str:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        return self.fernet.encrypt(payload).decode()

    def decrypt_json(self, token: str):
        try:
            return json.loads(self.fernet.decrypt(token.encode()).decode())
        except InvalidToken as exc:
            raise SecretError(
                t('server.secrets.decrypt_json.039dc173b')
            ) from exc
