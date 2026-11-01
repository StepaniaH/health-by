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


def raw_coverage(conn) -> dict:
    """按 HRV 记录日、睡眠本地结束日去重，不把载荷所在行日期当测量日期。"""
    raw_dates = set()
    for row in conn.execute("SELECT payload FROM raw_days"):
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            continue
        for point in _points(payload, "hrvRaw"):
            record = point.get("dailyHeartRateVariability")
            if isinstance(record, dict):
                recorded = _valid_date(_date_from_civil(record.get("date")))
                if recorded:
                    raw_dates.add(recorded)
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
                raw_dates.add(ended)
    normalized = {row[0] for row in conn.execute("SELECT date FROM days")}
    missing = sorted(raw_dates - normalized)
    return {
        "raw_days": len(raw_dates),
        "normalized_days": len(normalized),
        "missing": missing[:MAX_MISSING_DATES],
        "missing_count": len(missing),
    }
