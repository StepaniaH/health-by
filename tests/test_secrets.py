"""SecretStore 与外部密钥配置测试。"""

from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from health_by.config import load_config
from health_by.secrets import SecretError, SecretStore


def test_secret_store_prefers_valid_env_key_without_touching_disk(tmp_path):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / "nested" / "secret.key"
    store = SecretStore(key_path, env_key=key)

    token = store.encrypt_json({"client_secret": "s3cr3t"})
    assert store.decrypt_json(token) == {"client_secret": "s3cr3t"}
    assert not key_path.exists()


def test_secret_store_rejects_malformed_env_key(tmp_path):
    store = SecretStore(tmp_path / "secret.key", env_key="not-a-valid-fernet-key")
    with pytest.raises(SecretError):
        store.encrypt_json({"k": "v"})


def test_secret_store_creates_0600_key_file_atomically(tmp_path):
    key_path = tmp_path / "secret.key"
    store = SecretStore(key_path)
    token = store.encrypt_json({"refresh_token": "tok"})
    assert key_path.exists()
    assert (key_path.stat().st_mode & 0o777) == 0o600
    assert SecretStore(key_path).decrypt_json(token) == {"refresh_token": "tok"}


def test_config_honors_health_by_secret_key_path(tmp_path, monkeypatch):
    custom_key = tmp_path / "secrets" / "fernet.key"
    monkeypatch.setenv("HEALTH_BY_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("HEALTH_BY_SECRET_KEY_PATH", str(custom_key))
    cfg = load_config()
    assert cfg.secret_key_path == custom_key
