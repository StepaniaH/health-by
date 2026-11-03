"""仅在临时目录验证旧库迁移、备份、回滚与配置兼容，不使用真实数据。"""

import json
import logging
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from health_by import db, storage
from health_by.api.app import create_app
from health_by.config import load_config
from health_by.legacy import DB_NAME, LEGACY_DB_NAME, migrate_legacy_database
from devtools.gen_demo_data import SyntheticProvider
from health_by.secrets import SecretStore


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch):
    for prefix in ('HEALTH_BY_', 'FITBIT_BY_'):
        for suffix in ('DATA', 'HOST', 'PORT', 'PACE_MS', 'SYNC_WORKERS'):
            monkeypatch.delenv(prefix + suffix, raising=False)


def legacy_data(directory):
    directory.mkdir(parents=True, exist_ok=True)
    old = directory / LEGACY_DB_NAME
    with closing(db.connect(old)) as conn:
        db.migrate(conn)
        provider = SyntheticProvider()
        raw = provider.fetch_raw('2026-10-25')
        storage.store_day(conn, provider.translate(raw), raw)
        storage.set_setting(conn, 'goals', {'steps': 8765, 'sleep_minutes': 470})
        encrypted = SecretStore(directory / 'secret.key').encrypt_json({'api_key': 'synthetic-secret'})
        with conn:
            conn.execute("INSERT INTO credentials VALUES ('ai', ?, 'now')", (encrypted,))
    return old


def counts(path):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        return {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('days', 'daily_metrics', 'raw_days', 'settings', 'credentials')}


def test_migration_preserves_records_raw_data_settings_and_credentials_and_can_roll_back(tmp_path, caplog):
    directory = tmp_path / 'legacy'
    old = legacy_data(directory)
    before = counts(old)
    key = (directory / 'secret.key').read_bytes()
    with caplog.at_level(logging.INFO):
        cfg = load_config(directory)
    backup = directory / (LEGACY_DB_NAME + '.bak')
    assert cfg.db_path.name == DB_NAME and not old.exists()
    assert counts(cfg.db_path) == counts(backup) == before
    assert (backup.stat().st_mode & 0o777) == 0o600
    assert (directory / 'secret.key').read_bytes() == key
    assert '数据库已迁移' in caplog.text
    app = create_app(cfg)
    try:
        api = TestClient(app)
        assert api.get('/api/v1/status').json()['days_stored'] == 1
        assert api.get('/api/v1/summary', params={'date': '2026-10-25'}).status_code == 200
        with closing(db.connect(cfg.db_path)) as conn:
            raw = conn.execute('SELECT payload FROM raw_days').fetchone()[0]
            assert isinstance(json.loads(raw), dict)
            assert storage.load_settings(conn)['goals']['steps'] == 8765
            secret = storage.load_credentials_raw(conn, 'ai')
            assert SecretStore(cfg.secret_key_path).decrypt_json(secret)['api_key'] == 'synthetic-secret'
    finally:
        app.state.engine.shutdown()
        app.state.engine.close()
        app.state.conn.close()
    # 真实回滚流程在副本执行：使用 .bak 恢复旧文件名，SQLite/旧 schema 仍可读。
    cfg.db_path.unlink()
    backup.rename(old)
    assert counts(old) == before
    assert load_config(directory).db_path.exists()  # 二次迁移也可用。


def test_existing_new_database_and_existing_backups_are_never_overwritten(tmp_path):
    old = legacy_data(tmp_path / 'both')
    new = old.with_name(DB_NAME)
    with closing(db.connect(new)) as conn:
        db.migrate(conn)
    original = old.read_bytes()
    migrate_legacy_database(old.parent)
    assert old.read_bytes() == original and counts(new)['days'] == 0
    assert not old.with_name(LEGACY_DB_NAME + '.bak').exists()
    new.unlink()
    existing_backup = old.with_name(LEGACY_DB_NAME + '.bak')
    existing_backup.write_bytes(b'keep this backup')
    migrate_legacy_database(old.parent)
    assert existing_backup.read_bytes() == b'keep this backup'
    assert counts(old.with_name(LEGACY_DB_NAME + '.bak.1'))['days'] == 1


def test_active_wal_database_is_not_renamed_and_does_not_accumulate_backups(tmp_path):
    old = legacy_data(tmp_path / 'active')
    conn = db.connect(old)
    try:
        conn.execute('PRAGMA wal_autocheckpoint=0')
        storage.set_setting(conn, 'extra', 'committed-in-wal')
        assert old.with_name(old.name + '-wal').stat().st_size > 0
        with pytest.raises(RuntimeError, match='停止旧服务'):
            migrate_legacy_database(old.parent)
        assert old.exists() and not old.with_name(DB_NAME).exists()
        assert not old.with_name(LEGACY_DB_NAME + '.bak').exists()
    finally:
        conn.close()
    migrate_legacy_database(old.parent)
    assert counts(old.with_name(DB_NAME))['days'] == 1


def test_invalid_database_stays_intact_and_no_empty_target_is_created(tmp_path):
    old = tmp_path / LEGACY_DB_NAME
    old.write_bytes(b'not a SQLite database')
    with pytest.raises(RuntimeError, match='未覆盖旧数据'):
        load_config(tmp_path)
    assert old.read_bytes() == b'not a SQLite database'
    assert not (tmp_path / DB_NAME).exists()
    assert not old.with_name(LEGACY_DB_NAME + '.bak').exists()


def test_legacy_environment_migrates_same_directory_even_from_another_working_directory(tmp_path, monkeypatch, caplog):
    directory = tmp_path / 'source'
    old = legacy_data(directory)
    other = tmp_path / 'elsewhere'
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setenv('FITBIT_BY_DATA', str(directory))
    cfg = load_config()
    assert cfg.data_dir == directory and cfg.db_path.exists() and not old.exists()
    assert not (other / 'data').exists()
    assert '已弃用' in caplog.text
    assert load_config().db_path == cfg.db_path  # 新名存在，旧变量仍能打开；不重复备份。
    assert len(list(directory.glob('*.bak*'))) == 1


def test_new_environment_wins_and_legacy_empty_directory_is_an_explicit_error(tmp_path, monkeypatch, caplog):
    old_dir, new_dir = tmp_path / 'old', tmp_path / 'new'
    monkeypatch.setenv('FITBIT_BY_DATA', str(old_dir))
    with pytest.raises(ValueError, match='该路径没有数据库'):
        load_config()
    assert not old_dir.exists()
    monkeypatch.setenv('HEALTH_BY_DATA', str(new_dir))
    assert load_config().data_dir == new_dir
    assert '使用 HEALTH_BY_DATA' in caplog.text
    assert not old_dir.exists()


def test_other_legacy_environment_variables_remain_supported(tmp_path, monkeypatch, caplog):
    for suffix, value in [('HOST', '0.0.0.0'), ('PORT', '45678'), ('PACE_MS', '321')]:
        monkeypatch.setenv('FITBIT_BY_' + suffix, value)
    cfg = load_config(tmp_path)
    assert (cfg.host, cfg.port, cfg.pace_ms) == ('0.0.0.0', 45678, 321)
    monkeypatch.setenv('HEALTH_BY_PORT', '45679')
    assert load_config(tmp_path).port == 45679
    assert '已弃用' in caplog.text


def test_committed_wal_from_a_stopped_process_is_included_in_the_backup(tmp_path):
    import subprocess
    import sys
    old = legacy_data(tmp_path / 'crashed')
    script = """import sqlite3, os, sys, json
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA wal_autocheckpoint=0')
conn.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('wal_value', json.dumps('included')))
conn.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, '-c', script, str(old)], check=True)
    assert old.with_name(old.name + '-wal').stat().st_size > 0
    migrate_legacy_database(old.parent)
    for name in (DB_NAME, LEGACY_DB_NAME + '.bak'):
        with closing(db.connect(old.with_name(name))) as conn:
            assert storage.get_setting(conn, 'wal_value') == 'included'
