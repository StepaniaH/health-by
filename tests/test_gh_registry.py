"""数据任务注册表测试（防止历史数据被数据源家族过滤掉）。"""

from __future__ import annotations

from health_by.providers.google_health.registry import RECORD_JOBS


def test_sleep_job_reads_all_sources():
    """回归：历史睡眠可能来自 HEALTH_KIT，限定 google-wearables 会让旧日期睡眠为 None。"""
    sleep_job = next(job for job in RECORD_JOBS if job.key == "sleepRaw")
    assert sleep_job.family == "all-sources"
    assert sleep_job.page_size and sleep_job.page_size >= 100
