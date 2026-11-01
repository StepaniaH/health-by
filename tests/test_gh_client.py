"""Google Health 客户端与 provider 测试（httpx.MockTransport，完全离线）。"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import httpx
import pytest

from health_by.providers.base import ProviderAuthError, ProviderError
from health_by.providers.google_health import client as gh
from health_by.providers.google_health.provider import GoogleHealthProvider

DAY = "2026-06-15"


def make_http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_response(payload, status_code: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=payload, headers=headers or {})


# ---------- OAuth ----------


def test_pkce_pair_is_valid_s256():
    verifier, challenge = gh.create_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge
    assert "+" not in verifier and "/" not in verifier


def test_build_auth_url_parameters():
    url = gh.build_auth_url("client-123", "http://127.0.0.1:39441/oauth/callback", "st4te", "ch4ll")
    assert url.startswith(gh.AUTHORIZE_URL + "?")
    for fragment in (
        "client_id=client-123",
        "code_challenge_method=S256",
        "code_challenge=ch4ll",
        "access_type=offline",
        "prompt=consent",
        "state=st4te",
        "googlehealth.sleep.readonly",
        "googlehealth.activity_and_fitness.readonly",
    ):
        assert fragment in url
    assert "127.0.0.1%3A39441" in url  # redirect_uri 已编码


def test_exchange_code_sets_expires_at():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return json_response({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    before = time.time()
    tokens = gh.exchange_code(
        make_http(handler), client_id="cid", client_secret="sec", redirect_uri="http://cb", code="c0de",
        code_verifier="v3r",
    )
    assert tokens["access_token"] == "at"
    assert tokens["refresh_token"] == "rt"
    assert tokens["expires_at"] >= before + 3500
    assert captured["url"] == gh.TOKEN_URL
    assert "grant_type=authorization_code" in captured["body"]
    assert "code_verifier=v3r" in captured["body"]


def test_refresh_invalid_grant_raises_auth_error():
    http = make_http(lambda request: json_response({"error": "invalid_grant"}, 400))
    with pytest.raises(ProviderAuthError):
        gh.refresh_tokens(http, client_id="cid", client_secret="sec", refresh_token="dead")


# ---------- 请求 ----------


def test_request_retries_on_429_with_retry_after():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response({"error": {"message": "quota"}}, 429, headers={"retry-after": "1"})
        return json_response({"ok": True})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0, sleep=sleeps.append)
    assert client.get("/users/me/identity", "token") == {"ok": True}
    assert calls["n"] == 2
    assert sleeps == [1]


def test_request_retry_backoff_without_header():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response({}, 429)
        return json_response({"ok": True})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0, sleep=sleeps.append)
    client.get("/x", "token")
    assert sleeps and 0 < sleeps[0] <= 30


def test_request_401_raises_auth_error():
    client = gh.GoogleHealthClient(
        make_http(lambda request: json_response({"error": {"message": "expired"}}, 401)), pace_ms=0
    )
    with pytest.raises(ProviderAuthError):
        client.get("/users/me/identity", "token")


def test_request_error_message_from_payload():
    client = gh.GoogleHealthClient(
        make_http(lambda request: json_response({"error": {"message": "ECG not available"}}, 403)), pace_ms=0
    )
    with pytest.raises(ProviderError) as excinfo:
        client.get("/x", "token")
    assert excinfo.value.status == 403
    assert "ECG not available" in str(excinfo.value)


# ---------- 数据读取 ----------


def test_list_data_paginates_and_builds_params():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        if "pageToken" not in str(request.url):
            return json_response({"dataPoints": [{"a": 1}], "nextPageToken": "p2"})
        return json_response({"dataPoints": [{"b": 2}]})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0)
    result = client.list_data("tok", "sleep", "sleep", "2026-06-01", "2026-06-16", family="google-wearables")
    assert result["dataPoints"] == [{"a": 1}, {"b": 2}]
    first = seen[0]
    assert "dataPoints:reconcile" in first
    assert "google-wearables" in first
    assert "sleep.interval.civil_end_time" in first


def test_list_data_list_op_has_no_family_param():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return json_response({"dataPoints": []})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0)
    client.list_data("tok", "steps", "interval", "2026-06-01", "2026-06-16", op="list")
    assert "dataSourceFamilies" not in seen[0]
    assert "dataPoints?" in seen[0]


def test_list_data_page_guard(monkeypatch):
    monkeypatch.setattr(gh, "PAGINATION_GUARD", 3)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({"dataPoints": [], "nextPageToken": "next"})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0)
    with pytest.raises(ProviderError):
        client.list_data("tok", "steps", "interval", "2026-06-01", "2026-06-16", op="list")
    assert calls["n"] == 3


def test_daily_rollup_body_uses_end_of_day():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode())
        return json_response({"rollupDataPoints": []})

    client = gh.GoogleHealthClient(make_http(handler), pace_ms=0)
    client.daily_rollup("tok", "steps", "2026-06-02", "2026-06-16")
    body = captured["body"]
    assert body["range"]["start"]["date"] == {"year": 2026, "month": 6, "day": 2}
    assert body["range"]["end"]["date"] == {"year": 2026, "month": 6, "day": 15}
    assert body["range"]["end"]["time"]["hours"] == 23
    assert body["range"]["end"]["time"]["seconds"] == 59
    assert body["windowSizeDays"] == 1


def test_build_filter_shapes():
    assert gh.build_filter("steps", "daily", "a", "b") == 'steps.date >= "a" AND steps.date < "b"'
    assert "sleep.interval.civil_end_time" in gh.build_filter("sleep", "sleep", "a", "b")
    assert (
        gh.build_filter("electrocardiogram", "ecg", "2026-01-01", "x")
        == 'electrocardiogram.interval.start_time >= "2026-01-01T00:00:00Z"'
    )
    assert "sample_time.civil_time" in gh.build_filter("blood-glucose", "sample", "a", "b")
    assert "interval.civil_start_time" in gh.build_filter("steps", "interval", "a", "b")
    assert "daily_resting_heart_rate" in gh.build_filter("daily-resting-heart-rate", "daily", "a", "b")


# ---------- provider ----------


def _routing_handler(*, fail_sleep: bool = False, unauthorized_identity: bool = False):
    def handler(request):
        url = str(request.url)
        if unauthorized_identity and "identity" in url:
            return json_response({"error": {"message": "auth"}}, 401)
        if fail_sleep and "/dataTypes/sleep/" in url:
            return json_response({"error": {"message": "sleep unavailable"}}, 403)
        if "oauth2/v3/userinfo" in url:
            return json_response({"name": "T", "picture": None})
        if "dailyRollUp" in url:
            return json_response({"rollupDataPoints": []})
        if "pairedDevices" in url:
            return json_response({"pairedDevices": []})
        return json_response({"dataPoints": []})

    return handler


def test_fetch_raw_runs_all_jobs_and_collects_errors():
    provider = GoogleHealthProvider(pace_ms=0)
    raw = provider.fetch_raw(
        DAY,
        credentials={"tokens": {"access_token": "tok"}},
        http=make_http(_routing_handler(fail_sleep=True)),
    )
    assert raw.stats["total"] == 31
    assert raw.stats["succeeded"] == 30
    assert "sleepRaw" not in raw.payload
    assert raw.errors == [{"key": "sleepRaw", "message": "sleep unavailable", "status": 403}]
    snapshot = provider.translate(raw)
    assert snapshot.sleep is None
    assert snapshot.coverage.is_partial


def test_fetch_raw_reports_progress():
    provider = GoogleHealthProvider(pace_ms=0)
    seen: list[tuple[int, int]] = []
    provider.fetch_raw(
        DAY,
        credentials={"tokens": {"access_token": "t"}},
        http=make_http(_routing_handler()),
        on_progress=lambda completed, total, key: seen.append((completed, total)),
    )
    assert seen[-1] == (31, 31)
    assert len(seen) == 31


def test_fetch_raw_aborts_on_401():
    provider = GoogleHealthProvider(pace_ms=0)
    with pytest.raises(ProviderAuthError):
        provider.fetch_raw(
            DAY,
            credentials={"tokens": {"access_token": "tok"}},
            http=make_http(_routing_handler(unauthorized_identity=True)),
        )


def test_fetch_raw_requires_access_token():
    provider = GoogleHealthProvider(pace_ms=0)
    with pytest.raises(ProviderAuthError):
        provider.fetch_raw(DAY, credentials={}, http=make_http(_routing_handler()))


def test_auth_status_reports_configuration():
    provider = GoogleHealthProvider(pace_ms=0)
    status = provider.auth_status(None)
    assert status["configured"] is False and status["connected"] is False
    status = provider.auth_status(
        {
            "client_id": "cid",
            "client_secret": "sec",
            "redirect_uri": "http://127.0.0.1:39441/oauth/callback",
            "tokens": {"access_token": "a", "refresh_token": "r"},
        }
    )
    assert status["configured"] is True
    assert status["connected"] is True
    assert status["needs_reconnect"] is False


# ---------- 暂时性失败的重试与退避 ----------


def _client_with(handler, max_retries=2):
    sleeps: list[float] = []
    client = gh.GoogleHealthClient(
        make_http(handler), pace_ms=0, max_retries=max_retries, sleep=sleeps.append
    )
    return client, sleeps


def test_transient_5xx_is_retried_and_then_succeeds():
    """503 是暂时性的，重试一次就好——不该直接判成"这天没数据"。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response({"error": {"message": "backend unavailable"}}, 503)
        return json_response({"dataPoints": [{"ok": True}]})

    client, sleeps = _client_with(handler)
    payload = client.get("/users/me/dataTypes/steps/dataPoints", "tok")
    assert payload["dataPoints"] == [{"ok": True}]
    assert calls["n"] == 2
    assert sleeps, "重试之间必须退避，不能立刻连发"


def test_retries_are_capped_and_surface_a_provider_error():
    """一直 500 时不能无限重试，最终要给出明确错误。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({"error": {"message": "boom"}}, 500)

    client, _ = _client_with(handler, max_retries=2)
    with pytest.raises(gh.ProviderError):
        client.get("/users/me/dataPoints", "tok")
    assert calls["n"] == 3, "首次 + 2 次重试"


def test_transport_error_is_retried():
    """连接层异常（超时/重置）同样属于暂时性失败。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset")
        return json_response({"ok": 1})

    client, _ = _client_with(handler)
    assert client.get("/x", "tok") == {"ok": 1}
    assert calls["n"] == 2


def test_client_errors_are_not_retried():
    """400 这类是我们自己请求写错了，重试只是浪费配额。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({"error": {"message": "bad filter"}}, 400)

    client, _ = _client_with(handler)
    with pytest.raises(gh.ProviderError):
        client.get("/x", "tok")
    assert calls["n"] == 1


def test_401_is_not_retried_and_asks_for_reconnect():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({"error": {"message": "unauthorized"}}, 401)

    client, _ = _client_with(handler)
    with pytest.raises(ProviderAuthError):
        client.get("/x", "tok")
    assert calls["n"] == 1, "授权失效重试没有意义"
