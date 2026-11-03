"""合成原始窗口：行日期、内嵌测量日期和睡眠结束日期不能混用。"""

import json

from health_by.coverage import raw_coverage
from devtools.sample_payload import civil_date
from health_by.util import shift_iso


def save_raw(conn, day, payload):
    conn.execute("INSERT INTO raw_days VALUES (?, 'synthetic', '', ?)", (day, json.dumps(payload)))


def normalize(conn, day):
    conn.execute("INSERT INTO days VALUES (?, 'synthetic', '', '{}')", (day,))


def test_raw_window_dates_are_deduplicated_and_sleep_belongs_to_local_end_day(conn):
    hrv = {"hrvRaw": {"dataPoints": [{"dailyHeartRateVariability": {"date": civil_date("2024-02-01")}}]}}
    # 重复留档同一读数，且载荷行日期不等于测量日期。
    save_raw(conn, "2024-02-08", hrv)
    save_raw(conn, "2024-02-09", {**hrv, "sleepRaw": {"dataPoints": [{"sleep": {"interval": {
        "startTime": "2024-02-01T20:00:00Z", "endTime": "2024-02-01T23:00:00Z", "endUtcOffset": "7200s",
    }}}]}})
    normalize(conn, "2024-02-02")
    assert raw_coverage(conn) == {"raw_days": 2, "normalized_days": 1, "missing": ["2024-02-01"], "missing_count": 1}
    normalize(conn, "2024-02-01")
    assert raw_coverage(conn)["missing"] == []
    assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 2


def test_missing_count_is_complete_while_the_list_is_sorted_and_limited(conn):
    dates = [shift_iso("2024-01-01", offset) for offset in range(45)]
    save_raw(conn, "2024-03-01", {"hrvRaw": {"dataPoints": [
        {"dailyHeartRateVariability": {"date": civil_date(day)}} for day in reversed(dates)
    ]}})
    result = raw_coverage(conn)
    assert result["missing_count"] == result["raw_days"] == 45
    assert result["missing"] == dates[:30]


def test_empty_and_malformed_records_do_not_fabricate_coverage(conn):
    save_raw(conn, "2024-02-02", {"hrvRaw": {"dataPoints": [None, {}, {"dailyHeartRateVariability": {"date": {"year": 2024, "month": 99, "day": 1}}}]},
                                      "sleepRaw": {"dataPoints": [{"sleep": {"interval": {"endTime": "bad"}}}]}})
    assert raw_coverage(conn) == {"raw_days": 0, "normalized_days": 0, "missing": [], "missing_count": 0}
