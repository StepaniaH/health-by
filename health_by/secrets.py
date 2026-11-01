"""加密凭据存储：Fernet（密钥 data/secret.key，0600）。"""

from __future__ import annotations

from health_by.i18n import t

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretError(Exception):
    """凭据加解密失败（密钥不匹配或数据损坏）。"""


class SecretStore:
    def __init__(self, key_path: str | Path):
        self.key_path = Path(key_path)
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._load_or_create_key())
        return self._fernet

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            self._tighten_permissions()
            return self.key_path.read_bytes().strip()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # 并发创建：直接用已有的
            return self.key_path.read_bytes().strip()
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

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
