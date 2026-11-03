"""端到端集成测试：空库 → 播种 → 同步 → 分析 → HTTP 返回 → 边界。

单元测试各自隔离得太干净，很多问题只在「整条链路串起来」时才出现：
字段在某一层被改名、某个返回体悄悄少了键、空库时 500 而不是 404。
这一层就负责这个——只断言「契约」，即客户端真正依赖的东西。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from health_by import storage
from health_by.api.app import SYNC_MAX_DAYS, create_app
from health_by.providers.base import RawDay
from devtools.gen_demo_data import DemoProvider
from health_by.util import iso_today, shift_iso


@pytest.fixture
def client(tmp_cfg, synthetic_provider):
    application = create_app(tmp_cfg)
    storage.set_setting(application.state.conn, "provider", synthetic_provider)
    yield TestClient(application)
    application.state.engine.shutdown()
    application.state.engine.close()
    application.state.conn.close()


def _wait_idle(client: TestClient, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get("/api/v1/status").json()["sync"]["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("同步在超时内没有结束")


# ---------- 主链路 ----------


def test_full_chain_from_empty_database(client, synthetic_provider):
    """空库 → 播种 → 同步 → 报告 → 洞察，整条链路的契约都要成立。"""

    # 1) 空库：健康检查能过，状态可读，不能 500
    assert client.get("/api/v1/health").status_code == 200
    status = client.get("/api/v1/status").json()
    assert status["sync"]["running"] is False
    assert status["days_stored"] == 0
    assert status["latest_date"] is None
    assert client.get("/api/v1/dates").json()["dates"] == []

    # 2) 空库查当天数据：应该是 404（明确的"没数据"），不是 500
    assert client.get("/api/v1/summary").status_code == 404

    # 3) 播种
    seeded = client.post("/api/v1/sync", json={"days": 10})
    assert seeded.status_code == 200
    assert seeded.json()["started"] is True
    _wait_idle(client)

    # 4) 日期列表有数据，且是升序
    dates = client.get("/api/v1/dates").json()["dates"]
    assert len(dates) == 10
    assert dates == sorted(dates)
    assert dates[-1] == iso_today()

    # 5) 报告：关键字段存在且类型正确
    latest = dates[-1]
    summary = client.get("/api/v1/summary", params={"date": latest})
    assert summary.status_code == 200
    body = summary.json()
    assert body["date"] == latest
    assert isinstance(body["day"], dict)
    assert isinstance(body["trends"], list) and body["trends"]
    assert isinstance(body["goals"], dict)
    activity = body["day"]["activity"]
    assert isinstance(activity["steps"], (int, float))
    assert isinstance(body["analysis"], dict)

    # 趋势里每一天都要有 date 和步数
    for point in body["trends"]:
        assert isinstance(point["date"], str)
        assert isinstance(point["steps"], (int, float))

    # 6) 洞察：契约区块齐全，且数值字段类型正确
    insights = client.get("/api/v1/insights", params={"date": latest, "days": 30}).json()
    for key in ("from", "to", "n_days", "series", "correlations", "weekday", "heatmap", "bands", "timing", "achievements"):
        assert key in insights, f"insights 缺少 {key}"
    assert insights["to"] == latest
    assert isinstance(insights["series"], list)
    assert insights["n_days"] == len(insights["series"]) == 10
    # 覆盖率是"结论基于多少数据"的唯一凭据，必须在
    assert "coverage" in insights
    assert insights["coverage"]["days_with_archive"] == 10
    # 相关性每一条都要带样本量与显著性标记（审查过的那两条口径）
    for row in insights["correlations"]:
        assert "n" in row
        if "strength" in row:
            assert isinstance(row["strength"], str)

    # 7) 导出：能拿到全部内容
    exported = client.get("/api/v1/export").json()
    assert len(exported["days"]) == 10
    assert exported["settings"]["provider"] == synthetic_provider


# ---------- 边界 ----------


def test_single_day_chain_does_not_pretend_to_have_trends(client):
    """只有 1 天数据时，趋势和相关性都不该硬编结论。"""
    client.post("/api/v1/sync", json={"days": 1})
    _wait_idle(client)

    dates = client.get("/api/v1/dates").json()["dates"]
    assert len(dates) == 1

    summary = client.get("/api/v1/summary", params={"date": dates[0]}).json()
    assert len(summary["trends"]) == 1

    insights = client.get("/api/v1/insights", params={"date": dates[0]}).json()
    assert insights["n_days"] == 1
    # 1 天数据不足以谈相关性
    assert insights["correlations"] == []


def test_oversized_window_is_rejected_not_truncated_silently(client):
    """一次同步 400 天必须报错，而不是悄悄只做 90 天。"""
    response = client.post("/api/v1/sync", json={"days": 400})
    assert response.status_code == 400
    assert str(SYNC_MAX_DAYS) in response.json()["detail"]



def test_insights_window_is_clamped_to_supported_range(client):
    """insights 的 days 参数超出范围时被夹到 [7, 90]，不能把窗口开到 0 或无限大。"""
    client.post("/api/v1/sync", json={"days": 3})
    _wait_idle(client)
    latest = client.get("/api/v1/dates").json()["dates"][-1]

    tiny = client.get("/api/v1/insights", params={"date": latest, "days": 0}).json()
    assert tiny["window_days"] == 7

    huge = client.get("/api/v1/insights", params={"date": latest, "days": 10_000}).json()
    assert huge["window_days"] == 90


def test_day_with_no_usable_data_is_not_stored(conn, tmp_cfg):
    """整天全空（上游没数据）不该入库，否则会污染日期选择和趋势。"""
    from health_by.engine import Engine

    class AllEmpty(DemoProvider):
        def translate(self, raw):
            snapshot = super().translate(raw)
            snapshot.sleep = None
            snapshot.workouts = []
            for attribute in ("steps", "calories_out", "distance_km", "floors", "active_minutes",
                              "zone_minutes", "sedentary_minutes"):
                setattr(snapshot.activity, attribute, None)
            for attribute in ("resting_hr", "hrv_ms", "spo2", "breathing_rate",
                              "skin_temp_delta_c", "core_temp_c", "vo2max"):
                setattr(snapshot.heart, attribute, None)
            for attribute in ("weight_kg", "body_fat_pct", "water_ml", "calories_in"):
                setattr(snapshot.body, attribute, None)
            return snapshot

    engine = Engine(tmp_cfg, conn)
    try:
        from health_by.providers import PROVIDERS, ensure_builtin_providers
        import unittest.mock as mock

        ensure_builtin_providers()
        with mock.patch.dict(PROVIDERS, {"demo": AllEmpty()}):
            day = shift_iso(iso_today(), -1)
            snapshot = engine.sync_day(day)
            assert storage.get_day(conn, day) is None
            assert storage.count_days(conn) == 0
            # 但同步记录要留：这是"确实没数据"，不是失败
            run = storage.list_sync_runs(conn, limit=1)[0]
            assert run["status"] == "empty"
            assert run["date"] == day
    finally:
        engine.shutdown()
        engine.close()


def test_repeated_same_day_sync_is_idempotent(tmp_cfg, conn):
    """同一天反复同步：指标不重复、值不漂移。"""
    from health_by.engine import Engine

    engine = Engine(tmp_cfg, conn)
    first = engine.sync_day("2026-07-01")
    rows_once = conn.execute(
        "SELECT COUNT(*) AS n FROM daily_metrics WHERE date = ?", ("2026-07-01",)
    ).fetchone()["n"]

    for _ in range(3):
        engine.sync_day("2026-07-01")

    rows_many = conn.execute(
        "SELECT COUNT(*) AS n FROM daily_metrics WHERE date = ?", ("2026-07-01",)
    ).fetchone()["n"]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT metric) AS n FROM daily_metrics WHERE date = ?", ("2026-07-01",)
    ).fetchone()["n"]

    assert rows_once == rows_many == distinct
    # 反复同步不应改变已经正确的值
    assert storage.get_day(conn, "2026-07-01") == first.to_dict()
    # 每次同步都有记录，历史不会丢
    assert len(storage.list_sync_runs(conn, limit=10)) >= 4
    engine.shutdown()
    engine.close()
