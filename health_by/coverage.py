"""比较原始窗口中记录的日期与已归一化日期；只读，不自动回填。"""

import json
from datetime import date

from .providers.google_health.translate import _civil_date, _date_from_civil, _points

MAX_MISSING_DATES = 30


def _valid_date(value: str | None) -> str | None:
    try:
        return value if value and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def measure_dates(payload) -> set[str]:
    """载荷里提到的测量日期：HRV 记录日 + 睡眠本地结束日。

    不把载荷所在的行日期当测量日期——一个载荷带的是滚动窗口。
    """
    found: set[str] = set()
    if not isinstance(payload, dict):
        return found
    for point in _points(payload, "hrvRaw"):
        record = point.get("dailyHeartRateVariability")
        if isinstance(record, dict):
            recorded = _valid_date(_date_from_civil(record.get("date")))
            if recorded:
                found.add(recorded)
    for point in _points(payload, "sleepRaw"):
        sleep = point.get("sleep")
        interval = sleep.get("interval") if isinstance(sleep, dict) else None
        if not isinstance(interval, dict):
            continue
        ended = _valid_date(
            _date_from_civil(interval.get("civilEndTime"))
            or _civil_date(interval.get("endTime"), interval.get("endUtcOffset"))
        )
        if ended:
            found.add(ended)
    return found


def index_raw_dates(conn, only_unindexed: bool = True) -> int:
    """把载荷里的测量日期写进 raw_measure_dates；返回处理的行数。

    解析一份载荷要几十毫秒到几秒，所以只在写入时做一次，读取时查表。
    没有待处理行时不写数据库，调用方（接口缓存）才不会每次都被判为已变更。
    """
    sql = "SELECT date, payload FROM raw_days"
    if only_unindexed:
        sql += " WHERE date NOT IN (SELECT raw_date FROM raw_measure_dates)"
    rows = conn.execute(sql).fetchall()
    if not rows:
        return 0
    for row in rows:
        conn.execute("DELETE FROM raw_measure_dates WHERE raw_date = ?", (row[0],))
        for measured in measure_dates(json.loads(row[1])):
            conn.execute(
                "INSERT OR IGNORE INTO raw_measure_dates (raw_date, measure_date) VALUES (?, ?)",
                (row[0], measured),
            )
    conn.commit()
    return len(rows)


def forget_raw_dates(conn, raw_date: str) -> None:
    conn.execute("DELETE FROM raw_measure_dates WHERE raw_date = ?", (raw_date,))


def raw_coverage(conn) -> dict:
    """测量日期来自 raw_measure_dates；表里没有的旧行会被补建一次。"""
    index_raw_dates(conn)
    # 原始行被删掉时（清空数据、换源），派生行留在表里也无害：查询时按现存原始行过滤。
    raw_dates = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT measure_date FROM raw_measure_dates "
            "WHERE raw_date IN (SELECT date FROM raw_days)"
        )
    }
    normalized = {row[0] for row in conn.execute("SELECT date FROM days")}
    missing = sorted(raw_dates - normalized)
    return {
        "raw_days": len(raw_dates),
        "normalized_days": len(normalized),
        "missing": missing[:MAX_MISSING_DATES],
        "missing_count": len(missing),
    }
