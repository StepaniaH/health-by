"""Build a local development database from the synthetic generator.

The product has no demo provider: the generator lives here, outside the packaged
`health_by` module, and registers itself under the name "synthetic" for this run only.

    python -m devtools.make_dev_data                 # ./dev-data, last 30 days
    python -m devtools.make_dev_data --days 90 --out /tmp/dev
    HEALTH_BY_DATA=./dev-data python -m health_by.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from health_by import db, storage
from health_by.config import Config
from health_by.engine import Engine
from health_by.legacy import LEGACY_DB_NAME
from health_by.providers import PROVIDERS, ensure_builtin_providers
from health_by.secrets import SecretStore
from health_by.util import iso_range, iso_today

from devtools.gen_demo_data import SyntheticProvider


def build(out_dir: Path, days: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_builtin_providers()
    PROVIDERS["synthetic"] = SyntheticProvider()

    cfg = Config(data_dir=out_dir)
    legacy_db = out_dir / LEGACY_DB_NAME
    if legacy_db.exists() and not cfg.db_path.exists():
        print(
            f"Refusing to create {cfg.db_path.name} next to legacy {LEGACY_DB_NAME} in {out_dir}; "
            "use a dedicated dev directory or migrate first.",
            file=sys.stderr,
        )
        return 1
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    engine = Engine(cfg, conn)
    try:
        generated_dates = iso_range(iso_today(), max(1, days))
        # 保护目标库中已有的非 synthetic 记录（例如 Fitbit 导入或真实 Google Health 同步），
        # 即使落在本次生成区间内也跳过，绝不覆盖真实历史存档与原始载荷。
        existing_real = {
            row[0]
            for row in conn.execute(
                "SELECT date FROM days WHERE source != 'synthetic' "
                "UNION SELECT date FROM raw_days WHERE source != 'synthetic'"
            )
        }
        dates_to_generate = [date for date in generated_dates if date not in existing_real]
        skipped_real = len(generated_dates) - len(dates_to_generate)
        if not dates_to_generate:
            print(f"0/{len(generated_dates)} days written ({skipped_real} existing non-synthetic days preserved) in {cfg.db_path}")
            return 0
        storage.set_setting(conn, "provider", "synthetic")
        try:
            results = engine.sync_days(dates_to_generate)
        finally:
            # Leave the stored provider at its product default: the generator is not
            # registered in a normal run, so keeping "synthetic" would break `status`,
            # `serve` and the web app with an unknown-provider error.
            storage.set_setting(conn, "provider", storage.DEFAULT_SETTINGS["provider"])
        ok = sum(1 for item in results if item.get("status") != "error")
        # 仅改写本次合成生成的行，绝不污染同一库里已存在的其它来源历史记录（如 Fitbit 导入）。
        shape = storage.DEFAULT_SETTINGS["provider"]
        placeholders = ",".join("?" for _ in dates_to_generate)
        conn.execute(
            f"UPDATE days SET source = ? WHERE source = 'synthetic' AND date IN ({placeholders})",
            (shape, *dates_to_generate),
        )
        conn.execute(
            f"UPDATE raw_days SET source = ? WHERE source = 'synthetic' AND date IN ({placeholders})",
            (shape, *dates_to_generate),
        )
        # 快照 JSON 里也有一份 source，界面读的是那一份。
        for date, data in conn.execute(
            f"SELECT date, data FROM days WHERE date IN ({placeholders})",
            dates_to_generate,
        ).fetchall():
            try:
                snapshot = json.loads(data)
            except json.JSONDecodeError:
                continue
            if snapshot.get("source") == "synthetic":
                snapshot["source"] = shape
                conn.execute("UPDATE days SET data = ? WHERE date = ?",
                             (json.dumps(snapshot, ensure_ascii=False), date))
        conn.commit()
        # 页头会读 credentials 判断"是否已连接"。开发库放一份占位凭据，
        # 否则截图和页面顶上会一直挂着 "Google Health not connected"。
        if storage.load_credentials_raw(conn, shape) is None:
            store = SecretStore(cfg.data_dir / "secret.key")
            storage.save_credentials_raw(conn, shape, store.encrypt_json({
                "client_id": "dev-placeholder.apps.googleusercontent.com",
                "client_secret": "dev-placeholder",
                "tokens": {"refresh_token": "dev-placeholder"},
            }))
            conn.commit()
        if skipped_real:
            print(f"{ok}/{len(results)} days written ({skipped_real} existing non-synthetic days preserved) to {cfg.db_path}")
        else:
            print(f"{ok}/{len(results)} days written to {cfg.db_path}")
        return 0 if ok else 1
    finally:
        engine.close()
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="days back from today (default 30)")
    parser.add_argument("--out", type=Path, default=Path("dev-data"),
                        help="data directory to write (default ./dev-data)")
    args = parser.parse_args(argv)
    return build(args.out, args.days)


if __name__ == "__main__":
    sys.exit(main())
