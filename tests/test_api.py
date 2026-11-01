"""HTTP API 测试（TestClient，演示数据源，完全离线）。"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from health_by import storage
from health_by.api.app import create_app
from health_by.util import shift_iso

DAY = "2026-06-15"


@pytest.fixture
def app(tmp_cfg):
    application = create_app(tmp_cfg)
    yield application
    application.state.engine.shutdown()
    application.state.engine.close()
    application.state.conn.close()


@pytest.fixture
def client(app):
    # 不使用上下文管理器 → 不触发 lifespan（不自动播种），由测试自行准备数据
    return TestClient(app)


def seed(app, dates=None) -> None:
    app.state.engine.sync_days(dates or [DAY])


# ---------- 状态 ----------


def test_health(client):
    payload = client.get("/api/v1/health").json()
    assert payload["status"] == "ok"
    assert payload["version"]


def test_status_defaults_to_demo(client):
    payload = client.get("/api/v1/status").json()
    assert payload["provider"]["provider"] == "demo"
    assert payload["provider"]["auth_kind"] == "none"
    assert payload["goals"]["steps"] == 8000


def test_coverage_api_is_read_only_and_uses_embedded_dates(app, client):
    import json
    from health_by.providers._sample import civil_date

    app.state.conn.execute("INSERT INTO raw_days VALUES (?, 'synthetic', '', ?)", (DAY, json.dumps({
        "hrvRaw": {"dataPoints": [{"dailyHeartRateVariability": {"date": civil_date(shift_iso(DAY, -1))}}]},
    })))
    result = client.get("/api/v1/coverage").json()
    assert result == {"raw_days": 1, "normalized_days": 0, "missing": [shift_iso(DAY, -1)], "missing_count": 1}
    assert storage.count_days(app.state.conn) == 0


def test_coverage_cache_refreshes_after_local_and_other_connection_writes(app, client, monkeypatch):
    import importlib
    import json
    from health_by.db import connect
    from health_by.providers._sample import civil_date

    api = importlib.import_module("health_by.api.app")
    original = api.raw_coverage
    scans = []
    def counted(conn):
        scans.append(1)
        return original(conn)
    monkeypatch.setattr(api, "raw_coverage", counted)
    assert client.get("/api/v1/coverage").json()["raw_days"] == 0
    assert client.get("/api/v1/coverage").json()["raw_days"] == 0
    assert len(scans) == 1
    seed(app)
    summary = client.get("/api/v1/summary", params={"date": DAY}).json()
    assert client.get("/api/v1/coverage").json()["normalized_days"] == 1
    assert summary["day"]["coverage"]["normalized_days"] == 1
    assert len(scans) == 2
    other = connect(app.state.cfg.db_path)
    try:
        other.execute("INSERT INTO raw_days VALUES (?, 'synthetic', '', ?)", ("2024-01-01", json.dumps({
            "hrvRaw": {"dataPoints": [{"dailyHeartRateVariability": {"date": civil_date("2024-01-01")}}]},
        })))
        other.commit()
    finally:
        other.close()
    assert "2024-01-01" in client.get("/api/v1/coverage").json()["missing"]
    assert len(scans) == 3
    app.state.conn.execute("DELETE FROM raw_days")
    assert client.get("/api/v1/coverage").json()["raw_days"] == 0
    app.state.conn.rollback()
    assert "2024-01-01" in client.get("/api/v1/coverage").json()["missing"]
    assert len(scans) == 5


# ---------- 数据 ----------


def test_summary_404_before_data(client):
    response = client.get("/api/v1/summary", params={"date": DAY})
    assert response.status_code == 404


def test_summary_after_seed(app, client):
    seed(app)
    payload = client.get("/api/v1/summary", params={"date": DAY}).json()
    assert payload["date"] == DAY
    assert payload["day"]["date"] == DAY
    assert isinstance(payload["trends"], list) and payload["trends"]
    assert "headline" in payload["analysis"]
    coverage = client.get("/api/v1/coverage").json()
    assert {key: payload["day"]["coverage"][key] for key in coverage} == coverage
    assert "endpoint_count" in payload["day"]["coverage"]


def test_summary_defaults_to_latest(app, client):
    seed(app, ["2026-06-14", DAY])
    payload = client.get("/api/v1/summary").json()
    assert payload["date"] == DAY


def test_summary_invalid_date(client):
    assert client.get("/api/v1/summary", params={"date": "not-a-date"}).status_code == 400


def test_summary_and_insights_use_the_same_full_baseline_but_keep_short_chart_trends(app, client):
    """通过真实取数路径核对基线，不能给两个纯函数喂同一数组代替接口测试。"""
    from health_by import metrics
    from health_by.insights import SUMMARY_TREND_DAYS
    from health_by.models import DaySnapshot, HeartDay
    from health_by.providers.base import RawDay

    for index in range(30):
        day = DaySnapshot(
            shift_iso(DAY, index - 29),
            "demo",
            "2026-06-15T00:00:00Z",
            heart=HeartDay(
                resting_hr=60 if index < 16 else (40 if index % 2 == 0 else 80),
                hrv_ms=40 if index < 16 else (10 if index % 2 == 0 else 70),
            ),
        )
        storage.store_day(app.state.conn, day, RawDay(day.date, day.source, day.generated_at, {}))

    summary_response = client.get("/api/v1/summary", params={"date": DAY})
    insights_response = client.get("/api/v1/insights", params={"date": DAY})
    assert summary_response.status_code == insights_response.status_code == 200
    summary = summary_response.json()
    recovery = insights_response.json()["recovery"]
    for key, recovery_key, expected in (
        ("resting_hr", "rhr_baseline", 60),
        ("hrv_ms", "hrv_baseline", 40),
    ):
        comparison = summary["analysis"]["comparisons"][key]
        assert comparison["baseline"] == recovery[recovery_key] == expected
        assert comparison["sample_count"] == metrics.BASELINE_WINDOW_DAYS
        assert comparison["break"] is None

    assert len(summary["trends"]) == SUMMARY_TREND_DAYS
    assert summary["trends"][0]["date"] == shift_iso(DAY, -(SUMMARY_TREND_DAYS - 1))
    assert summary["trends"][-1]["date"] == DAY


def test_summary_statistical_break_does_not_shorten_baseline(app, client):
    from health_by.models import DaySnapshot, HeartDay
    from health_by.providers.base import RawDay

    for index in range(29):
        selected = shift_iso(DAY, index - 28)
        snapshot = DaySnapshot(selected, "synthetic", "2024-01-01T00:00:00Z",
                               heart=HeartDay(resting_hr=60 if index < 14 else 80))
        storage.store_day(app.state.conn, snapshot, RawDay(selected, "synthetic", snapshot.generated_at, {}))
    result = client.get("/api/v1/summary", params={"date": DAY}).json()
    comparison = result["analysis"]["comparisons"]["resting_hr"]
    assert comparison["valid_from"] == shift_iso(DAY, -28)
    assert comparison["break"] is None
    assert comparison["field_appearance"] is None
    assert comparison["baseline"] == 70 and comparison["sample_count"] == 28
    missing = result["analysis"]["comparisons"]["hrv_ms"]
    assert missing["baseline"] is None and missing["insufficient_reason"]



def test_summary_field_appearance_uses_archived_missing_days_and_excludes_selected_day(app, client):
    from health_by.models import ActivityDay, DaySnapshot, HeartDay
    from health_by.providers.base import RawDay

    for index in range(29):
        selected = shift_iso(DAY, index - 28)
        snapshot = DaySnapshot(selected, "synthetic", "2024-01-01T00:00:00Z",
                               activity=ActivityDay(steps=1000),
                               heart=HeartDay(hrv_ms=None if index < 14 else (999 if index == 28 else 20)))
        storage.store_day(app.state.conn, snapshot, RawDay(selected, "synthetic", snapshot.generated_at, {}))
    comparison = client.get("/api/v1/summary", params={"date": DAY}).json()["analysis"]["comparisons"]["hrv_ms"]
    assert comparison["valid_from"] == comparison["field_appearance"] == shift_iso(DAY, -14)
    assert comparison["break"] is None
    assert comparison["baseline"] == 20 and comparison["sample_count"] == 14


def test_summary_reliability_uses_normalized_days_and_keeps_behavior_daily(app, client):
    from health_by.models import ActivityDay, DaySnapshot, HeartDay
    from health_by.providers.base import RawDay

    for index in range(56):
        selected = shift_iso(DAY, index - 55)
        snapshot = DaySnapshot(selected, "synthetic", "2024-01-01T00:00:00Z",
                               activity=ActivityDay(steps=1000 + (index % 7) * 500),
                               heart=HeartDay(hrv_ms=31 + (index % 7) * 4, resting_hr=60 + index // 7))
        storage.store_day(app.state.conn, snapshot, RawDay(selected, "synthetic", snapshot.generated_at, {}))
    result = client.get("/api/v1/summary", params={"date": DAY}).json()
    data = result["analysis"]
    expected = {"steps", "sleep_minutes", "resting_hr", "hrv_ms", "breathing_rate", "spo2", "skin_temp_delta"}
    assert set(data["comparisons"]) == set(data["reliability"]) == set(data["display_mode"]) == expected
    assert data["reliability"]["hrv_ms"] < 0.4 and data["display_mode"]["hrv_ms"] == "rolling7"
    assert data["display_values"]["hrv_ms"] == 43
    assert result["day"]["heart"]["hrv_ms"] == 55  # 存档与日值不被平滑覆盖。
    assert data["reliability"]["steps"] < 0.4 and data["display_mode"]["steps"] == "daily"
    assert data["display_values"]["steps"] == result["day"]["activity"]["steps"] == 4000
    assert data["display_mode"]["resting_hr"] == "daily"
    for key in expected:
        item = data["comparisons"][key]
        assert item["current"] == data["display_values"][key]
        assert item["break"] is None
        if data["display_mode"][key] == "daily":
            assert item["current"] == item["current_daily"]
        if item["baseline"] is not None and item["current"] is not None:
            assert item["difference"] == round(item["current"] - item["baseline"], 2)
            if item["baseline"] != 0:
                assert item["percent_change"] == round((item["current"] - item["baseline"]) / item["baseline"] * 100, 1)
    assert data["comparisons"]["hrv_ms"]["current_daily"] == 55
    hrv_insight = next(item for item in data["insights"] if item["id"] == "hrv")
    assert "近 7 日均值 43" in hrv_insight["body"] and "55" not in hrv_insight["body"]
    assert "HRV近 7 日均值 43" in data["headline"]["body"]
    assert data["reliability"]["spo2"] is None and data["insufficient"]["spo2"] is True
    assert data["display_mode"]["spo2"] == "daily"
    basis = data["reliability_basis"]
    assert basis["window_days"] == 56 and basis["min_weeks"] == 3 and basis["threshold"] == 0.4
    assert basis["method"] == "variance_ratio"
    assert set(basis["weeks"]) == set(basis["mean_days_per_week"]) == expected
    assert basis["weeks"]["hrv_ms"] >= 3 and 3 <= basis["mean_days_per_week"]["hrv_ms"] <= 7
    assert basis["weeks"]["spo2"] == 0 and basis["mean_days_per_week"]["spo2"] is None


# ---------- 同步 ----------


def test_sync_background_flow(app, client):
    response = client.post("/api/v1/sync", json={"date": DAY})
    assert response.status_code == 200
    body = response.json()
    assert body["started"] is True
    assert body["dates"] == [DAY]
    deadline = time.time() + 10
    while True:
        status = client.get("/api/v1/sync/status").json()
        if not status["running"]:
            break
        assert time.time() < deadline, "同步超时"
        time.sleep(0.05)
    assert storage.get_day(app.state.conn, DAY) is not None


def test_sync_queue_accepts_second_request(client):
    first = client.post("/api/v1/sync", json={"date": DAY})
    second = client.post("/api/v1/sync", json={"date": DAY})
    assert first.status_code == 200
    assert second.status_code == 200  # 已有任务时排队，不再 409
    deadline = time.time() + 10
    while client.get("/api/v1/sync/status").json()["running"]:
        assert time.time() < deadline, "同步超时"
        time.sleep(0.05)


def test_sync_accepts_date_range(client):
    end = DAY
    start = shift_iso(DAY, -2)
    response = client.post("/api/v1/sync", json={"start": start, "end": end})
    assert response.status_code == 200
    assert response.json()["dates"] == [shift_iso(DAY, -2), shift_iso(DAY, -1), DAY]
    deadline = time.time() + 15
    while client.get("/api/v1/sync/status").json()["running"]:
        assert time.time() < deadline, "同步超时"
        time.sleep(0.05)


def test_sync_accepts_date_list(client):
    response = client.post("/api/v1/sync", json={"dates": [DAY, shift_iso(DAY, -1)]})
    assert response.status_code == 200
    assert response.json()["dates"] == [DAY, shift_iso(DAY, -1)]


def test_sync_range_over_limit_rejected(client):
    start = shift_iso(DAY, -90)
    response = client.post("/api/v1/sync", json={"start": start, "end": DAY})
    assert response.status_code == 400


def test_sync_days_limit(client):
    assert client.post("/api/v1/sync", json={"days": 999}).status_code == 400


# ---------- 设置 ----------


def test_settings_update_and_validation(client):
    response = client.post(
        "/api/v1/settings",
        json={"goals": {"steps": 9000, "sleep_minutes": 450}, "sync_interval_minutes": 120},
    )
    payload = response.json()
    assert payload["goals"] == {"steps": 9000, "sleep_minutes": 450}
    assert payload["sync_interval_minutes"] == 120
    assert client.post("/api/v1/settings", json={"goals": {"steps": -1}}).status_code == 400
    assert client.post("/api/v1/settings", json={"provider": "nope"}).status_code == 400


def test_provider_switch(client):
    assert client.post("/api/v1/settings", json={"provider": "google-health"}).json()["provider"] == "google-health"
    assert client.post("/api/v1/settings", json={"provider": "demo"}).json()["provider"] == "demo"


def test_credentials_masked_in_settings(client):
    response = client.post(
        "/api/v1/settings/credentials", json={"client_id": "cid-1", "client_secret": "sec-1"}
    )
    block = response.json()["google_health"]
    assert block["configured"] is True
    assert block["client_id"] == "cid-1"
    assert block["has_client_secret"] is True
    assert "sec-1" not in response.text


def test_credentials_keep_existing_secret(client):
    client.post("/api/v1/settings/credentials", json={"client_id": "cid", "client_secret": "sec"})
    response = client.post("/api/v1/settings/credentials", json={"client_id": "cid2"})
    block = response.json()["google_health"]
    assert block["client_id"] == "cid2"
    assert block["has_client_secret"] is True


# ---------- OAuth ----------


def test_oauth_start_requires_credentials(client):
    assert client.get("/oauth/start", follow_redirects=False).status_code == 400


def test_oauth_start_redirects_to_google(client):
    client.post("/api/v1/settings/credentials", json={"client_id": "cid", "client_secret": "sec"})
    response = client.get("/oauth/start", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid" in location
    assert "code_challenge_method=S256" in location


def test_oauth_callback_rejects_bad_state(client):
    response = client.get("/oauth/callback", params={"code": "x", "state": "bogus"}, follow_redirects=False)
    assert response.status_code == 400


def test_oauth_callback_exchanges_code(app, client, monkeypatch):
    client.post("/api/v1/settings/credentials", json={"client_id": "cid", "client_secret": "sec"})
    storage.save_oauth_state(app.state.conn, "state-ok", "verifier-ok")

    def handler(request):
        assert "oauth2.googleapis.com/token" in str(request.url)
        assert "code_verifier=verifier-ok" in request.content.decode()
        return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})

    app.state.engine._http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(app.state.engine, "start_background", lambda dates: True)

    response = client.get(
        "/oauth/callback", params={"code": "c0de", "state": "state-ok"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("/settings?oauth=ok")
    credentials = app.state.engine.credentials_for("google-health")
    assert credentials["tokens"]["access_token"] == "AT"
    assert credentials["needs_reconnect"] is False


# ---------- 数据管理 ----------


def test_export_download(app, client):
    seed(app)
    response = client.get("/api/v1/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert DAY in payload["days"]
    assert DAY in payload["raw_days"]


def test_clear_requires_confirmation(app, client):
    seed(app)
    assert client.post("/api/v1/data/clear", json={}).status_code == 400
    response = client.post("/api/v1/data/clear", json={"confirm": "DELETE ALL DATA"})
    assert response.json()["ok"] is True
    assert storage.count_days(app.state.conn) == 0


# ---------- 首启播种 ----------


def test_bootstrap_seeds_demo_on_startup(tmp_cfg):
    application = create_app(tmp_cfg)
    with TestClient(application) as seeded_client:
        data = seeded_client.get("/api/v1/status").json()
        assert data["days_stored"] >= 30
        summary = seeded_client.get("/api/v1/summary").json()
        assert summary["day"]["activity"]["steps"] is not None


# ---------- 关联与节律 ----------


def test_insights_endpoint(app, client):
    seed(app, ["2026-06-14", DAY])
    payload = client.get("/api/v1/insights", params={"date": DAY, "days": 30}).json()
    assert payload["to"] == DAY
    assert payload["n_days"] >= 2
    assert isinstance(payload["series"], list)
    # `notice` 已从前端与 insights 载荷中移除（零引用），断言随之去掉；
    # health_by/insights.py 里那个占位的 "notice": "" 也请随之删除。
    for key in ("correlations", "weekday", "heatmap", "bands", "timing", "achievements"):
        assert key in payload
    assert client.get("/api/v1/insights", params={"date": "bad"}).status_code == 400


# ---------- 演示数据播种 ----------


def test_demo_seed_reports_queue_instead_of_dead_409(app, client):
    """`start_background` 永远返回 dict，所以原来那个 409 永远不会触发。

    既然排队语义是「已有任务在跑就继续排队」，播种接口就该如实回报排队情况，
    而不是留一条永远走不到的拒绝分支。
    """
    response = client.post("/api/v1/demo/seed", json={"days": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    assert payload["days"] == 3
    assert payload["added"] == 3
    assert payload["outstanding"] >= 1

    deadline = time.time() + 20
    while time.time() < deadline and app.state.engine.is_running():
        time.sleep(0.05)
    assert not app.state.engine.is_running()


def test_demo_seed_rejects_out_of_range_days(client):
    assert client.post("/api/v1/demo/seed", json={"days": 0}).status_code == 400


def test_insights_hides_uncorrected_correlations_by_default(app, client):
    """默认只下发通过校正的相关；未通过的只以计数表达，供调试的开关默认关闭。"""
    from health_by.util import shift_iso

    # 必须喂够天数：MIN_CORRELATION_SAMPLES=10，只给 2 天时根本没有相关性可检验，
    # 那样这条测试会"通过"却什么都没验证（第一版就是这么写的，被自检抓出来）。
    seed(app, [shift_iso(DAY, -offset) for offset in range(14)])
    default = client.get("/api/v1/insights", params={"date": DAY, "days": 30}).json()
    debug = client.get(
        "/api/v1/insights", params={"date": DAY, "days": 30, "include_hidden": 1}
    ).json()

    # 两种模式都必须给出"检验了几组 / 隐藏了几组"这两个事实
    assert "correlations_tested" in default and "correlations_hidden" in default
    assert default["correlations_tested"] == debug["correlations_tested"]
    # 前提：这个样本量下确实存在被隐藏的结论，否则下面的断言没有意义
    assert default["correlations_hidden"] > 0, "样本不足以产生被隐藏的相关，测试无效"

    # 默认模式：只留下通过校正的
    assert all(row["survives_correction"] for row in default["correlations"])
    assert len(default["correlations"]) + default["correlations_hidden"] == default["correlations_tested"]

    # 调试模式：全部返回，且能对上 tested
    assert len(debug["correlations"]) == debug["correlations_tested"]
    # `correlations_hidden` 是"有多少条没过标准"，属数据的属性，与请求参数无关：
    # 调试模式与默认模式必须是同一个数，否则最需要它的时候（排查隐藏了哪些）反而读到 0。
    assert debug["correlations_hidden"] == default["correlations_hidden"]
    # 反过来，"剩几条"只对默认视图有意义
    assert len(default["correlations"]) == default["correlations_tested"] - default["correlations_hidden"]
