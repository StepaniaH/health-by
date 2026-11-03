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
from health_by.providers import PROVIDERS, ensure_builtin_providers
from health_by.util import iso_range, iso_today

from devtools.gen_demo_data import SyntheticProvider


def build(out_dir: Path, days: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_builtin_providers()
    PROVIDERS["synthetic"] = SyntheticProvider()

    cfg = Config(data_dir=out_dir)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    engine = Engine(cfg, conn)
    try:
        storage.set_setting(conn, "provider", "synthetic")
        results = engine.sync_days(iso_range(iso_today(), max(1, days)))
        ok = sum(1 for item in results if item.get("status") != "error")
        # Leave the stored provider at its product default: the generator is not
        # registered in a normal run, so keeping "synthetic" would break `status`,
        # `serve` and the web app with an unknown-provider error.
        storage.set_setting(conn, "provider", storage.DEFAULT_SETTINGS["provider"])
        # 行数据也标成正式来源：开发库要和真实部署长得一样，否则页头显示
        # "Google Health" 而每天写着别的来源，读的人会以为数据出了问题。
        shape = storage.DEFAULT_SETTINGS["provider"]
        conn.execute("UPDATE days SET source = ?", (shape,))
        conn.execute("UPDATE raw_days SET source = ?", (shape,))
        # 快照 JSON 里也有一份 source，界面读的是那一份。
        for date, data in conn.execute("SELECT date, data FROM days").fetchall():
            try:
                snapshot = json.loads(data)
            except json.JSONDecodeError:
                continue
            if snapshot.get("source") != shape:
                snapshot["source"] = shape
                conn.execute("UPDATE days SET data = ? WHERE date = ?",
                             (json.dumps(snapshot, ensure_ascii=False), date))
        conn.commit()
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
