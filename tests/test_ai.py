"""AI 的真实接口/存储/HTTP 契约；所有上游请求都由 MockTransport 接住。"""

import json
import logging
import socket
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from health_by import storage
from health_by.ai import prompt
from health_by.ai.validation import RULES_VERSION, validate_output
from health_by.api.app import create_app
from health_by.util import iso_range, iso_today

HTTP_CLIENT = httpx.Client
KEY = "sk-test-DO-NOT-LEAK"


@pytest.fixture
def ai_app(tmp_cfg):
    app = create_app(tmp_cfg)
    yield app
    app.state.engine.shutdown()
    app.state.engine.close()
    app.state.conn.close()


@pytest.fixture
def api(ai_app):
    return TestClient(ai_app)


def configure(api, **patch):
    response = api.post("/api/v1/settings/ai", json={
        "ai_enabled": True, "ai_base_url": "https://model.example/v1",
        "ai_model": "test-model", "api_key": KEY, **patch,
    })
    assert response.status_code == 200, response.text
    return response.json()


def mock_model(monkeypatch, handler):
    monkeypatch.setattr("health_by.ai.client.httpx.Client", lambda **kwargs: HTTP_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


def good_reply():
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({
            "phenomenon": "当天步数 8000 步；近 28 天有 20 个基线样本。", "causes": None, "advice": None,
        }, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
    })


def test_disabled_ai_and_reading_settings_or_cache_never_call_a_model(ai_app, api, monkeypatch):
    mock_model(monkeypatch, lambda request: pytest.fail("读取配置/缓存不应调用模型"))
    assert api.get("/api/v1/settings").json()["ai"]["enabled"] is False
    assert api.get("/api/v1/ai/analysis", params={"date": iso_today()}).json()["analysis"] is None
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).status_code == 400


def test_key_is_encrypted_never_echoed_and_blank_keeps_it(ai_app, api):
    assert configure(api)["has_api_key"] is True
    raw = storage.load_credentials_raw(ai_app.state.conn, "ai")
    assert KEY not in raw
    assert ai_app.state.ai.secrets.decrypt_json(raw) == {"api_key": KEY}
    assert storage.get_setting(ai_app.state.conn, "api_key") is None
    assert api.post("/api/v1/settings/ai", json={"api_key": ""}).json()["has_api_key"] is True
    for path in ("/api/v1/settings", "/api/v1/status", "/api/v1/export"):
        assert KEY not in api.get(path).text
    assert api.post("/api/v1/settings/ai", json={"clear_api_key": True}).json()["has_api_key"] is False


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@localhost/v1", "https://model.example/v1?key=secret"])
def test_invalid_configuration_has_safe_errors_and_does_not_change_settings(ai_app, api, url):
    before = storage.load_settings(ai_app.state.conn)
    response = api.post("/api/v1/settings/ai", json={"ai_base_url": url, "api_key": KEY})
    assert response.status_code == 400
    assert "secret" not in response.text and KEY not in response.text
    assert storage.load_settings(ai_app.state.conn) == before


def test_generation_cache_force_model_version_and_request_privacy(ai_app, api, monkeypatch):
    configure(api)
    day = iso_today()
    ai_app.state.engine.sync_days(iso_range(day, 30))
    requests = []

    def handler(request):
        assert request.url == "https://model.example/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        requests.append(body)
        assert KEY not in request.content.decode()
        assert "演示用户" not in request.content.decode() and "demo-tracker" not in request.content.decode()
        data = json.loads(body["messages"][1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        assert data["date"] == "D-0"
        assert "steps_intraday" not in json.dumps(data)
        assert "start_time" not in json.dumps(data)
        assert body["stream"] is False
        return good_reply()

    mock_model(monkeypatch, handler)
    assert api.get("/api/v1/ai/analysis", params={"date": day}).json()["analysis"] is None
    first = api.post("/api/v1/ai/analyze", json={"date": day}).json()
    assert first["cached"] is False and first["model"] == "test-model"
    assert first["prompt_version"] == prompt.PROMPT_VERSION
    assert first["validation"]["rules_version"] == RULES_VERSION
    assert first["validation"]["validated_at"] == first["created_at"]
    assert first["usage"]["total_tokens"] == 130
    assert len(first["payload_hash"]) == 64
    cached = api.post("/api/v1/ai/analyze", json={"date": day}).json()
    assert cached["cached"] is True
    assert cached["validation"] == first["validation"]
    assert len(requests) == 1
    assert api.post("/api/v1/ai/analyze", json={"date": day, "force": True}).status_code == 200
    assert len(requests) == 2
    rows = ai_app.state.conn.execute("SELECT * FROM ai_analyses ORDER BY id").fetchall()
    assert len(rows) == 2  # 重算保留调用历史与各自用量。
    assert rows[0]["status"] == "ok"
    assert json.loads(rows[0]["validation_json"]) == first["validation"]
    exported = api.get("/api/v1/export").json()["ai_analyses"]
    assert json.loads(exported[0]["validation_json"]) == first["validation"]
    egress = ai_app.state.conn.execute("SELECT * FROM ai_egress ORDER BY id").fetchall()
    assert egress[0]["payload_keys_json"] == egress[1]["payload_keys_json"]
    assert "8000" not in egress[0]["payload_shapes_json"]
    assert api.post("/api/v1/settings/ai", json={"ai_model": "another-model"}).status_code == 200
    assert api.get("/api/v1/ai/analysis", params={"date": day}).json()["analysis"] is None
    api.post("/api/v1/settings/ai", json={"ai_model": "test-model"})
    monkeypatch.setattr(prompt, "PROMPT_VERSION", "future-version")
    assert api.get("/api/v1/ai/analysis", params={"date": day}).json()["analysis"] is None


@pytest.mark.parametrize("status,expected", [(401, 502), (500, 502), (429, 429), (200, 502)])
def test_provider_failures_are_safe_and_audited(ai_app, api, monkeypatch, caplog, status, expected):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())
    mock_model(monkeypatch, lambda request: httpx.Response(status, text=f"upstream echoed {KEY}"))
    with caplog.at_level(logging.DEBUG):
        response = api.post("/api/v1/ai/analyze", json={"date": iso_today()})
    assert response.status_code == expected
    assert KEY not in response.text and KEY not in caplog.text
    row = ai_app.state.conn.execute("SELECT * FROM ai_analyses").fetchone()
    assert row["status"] == "error" and row["content"] is None
    assert len(ai_app.state.conn.execute("SELECT payload_hash FROM ai_egress").fetchone()[0]) == 64
    assert api.get("/api/v1/ai/analysis", params={"date": iso_today()}).json()["analysis"] is None


def test_missing_day_busy_lock_and_data_clear(ai_app, api, monkeypatch):
    configure(api)
    mock_model(monkeypatch, lambda request: good_reply())
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).status_code == 404
    ai_app.state.engine.sync_day(iso_today())
    ai_app.state.ai._lock.acquire()
    try:
        assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).status_code == 409
    finally:
        ai_app.state.ai._lock.release()
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).status_code == 200
    assert len(api.get("/api/v1/export").json()["ai_analyses"]) == 1
    api.post("/api/v1/data/clear", json={"confirm": "DELETE ALL DATA"})
    assert ai_app.state.conn.execute("SELECT COUNT(*) FROM ai_analyses").fetchone()[0] == 0
    assert storage.load_credentials_raw(ai_app.state.conn, "ai") is not None


def test_timeout_and_even_successful_key_echo_are_sanitized(ai_app, api, monkeypatch):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())

    def timeout(request):
        raise httpx.ReadTimeout(KEY, request=request)

    mock_model(monkeypatch, timeout)
    response = api.post("/api/v1/ai/analyze", json={"date": iso_today()})
    assert response.status_code == 504 and KEY not in response.text

    def echo(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "phenomenon": f"当天步数 8000 步。{KEY}", "causes": None, "advice": None,
        })}}]})

    mock_model(monkeypatch, echo)
    result = api.post("/api/v1/ai/analyze", json={"date": iso_today()})
    assert result.status_code == 200 and KEY not in result.text
    assert result.json()["usage"]["total_tokens"] is None


@pytest.mark.parametrize("content", [
    {"phenomenon": "状态不错", "causes": None, "advice": None},
    {"phenomenon": "步数 8000 步，仅供参考", "causes": None, "advice": None},
    {"phenomenon": "步数 8000 步", "causes": "建议注意休息", "advice": None},
])
def test_unusable_output_is_marked_for_repair_instead_of_silently_accepted(content):
    _, validation = validate_output(json.dumps(content, ensure_ascii=False), {"steps": 8000})
    assert validation["state"] == "failed" and validation["issues"]


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_failed_output_gets_one_repair_and_each_request_is_separately_audited(
    ai_app, api, monkeypatch, repair_succeeds
):
    configure(api, ai_base_url="https://opencode.ai/zen/go/v1")
    snapshot = ai_app.state.engine.sync_day(iso_today())
    bad = {"phenomenon": "状态似乎不错。", "causes": None, "advice": None}
    fixed = {"phenomenon": f"当天步数 {snapshot.activity.steps} 步。", "causes": "依据步数记录分析。", "advice": None}
    requests = []
    sessions = []

    def handler(request):
        sessions.append(request.headers["x-opencode-session"])
        requests.append(json.loads(request.content))
        content = fixed if len(requests) == 2 and repair_succeeds else bad
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        })

    mock_model(monkeypatch, handler)
    response = api.post("/api/v1/ai/analyze", json={"date": iso_today()})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(requests) == result["attempts"] == 2
    assert [len(item["messages"]) for item in requests] == [2, 4]
    assert requests[1]["messages"][2] == {"role": "assistant", "content": json.dumps(bad, ensure_ascii=False)}
    assert result["validation"]["state"] == ("ok" if repair_succeeds else "failed")
    assert result["validation"]["rules_version"] == RULES_VERSION
    assert result["validation"]["validated_at"] == result["created_at"]
    assert result["content"] == (fixed if repair_succeeds else bad)
    assert result["usage"]["total_tokens"] == 260
    analyses = ai_app.state.conn.execute("SELECT * FROM ai_analyses").fetchall()
    egress = ai_app.state.conn.execute("SELECT * FROM ai_egress ORDER BY attempt").fetchall()
    assert len(analyses) == 1 and len(egress) == 2
    assert sessions[0] == sessions[1] == egress[0]["session_id"] == egress[1]["session_id"]
    assert UUID(sessions[0]).version == 4
    assert analyses[0]["validation_state"] == result["validation"]["state"]
    assert [row["attempt"] for row in egress] == [1, 2]
    assert egress[0]["payload_keys_hash"] == egress[1]["payload_keys_hash"]
    assert egress[0]["request_hash"] != egress[1]["request_hash"]
    assert egress[0]["assistant_echo_json"] is None
    assert json.loads(egress[1]["assistant_echo_json"])["kind"] == "model_generated_text"
    assert "状态似乎不错" not in egress[1]["assistant_echo_json"]  # 留痕不复制模型正文。
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).json()["cached"] is True
    assert len(requests) == 2


def test_repair_network_failure_preserves_the_first_reply(ai_app, api, monkeypatch):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "phenomenon": "状态不错。", "causes": None, "advice": None,
        }, ensure_ascii=False)}}]})

    mock_model(monkeypatch, handler)
    result = api.post("/api/v1/ai/analyze", json={"date": iso_today()}).json()
    assert calls == 2 and result["content"]["phenomenon"] == "状态不错。"
    assert result["validation"]["state"] == "failed"
    assert any(item["section"] == "retry" for item in result["validation"]["issues"])


def test_escaped_key_echo_cannot_leak_into_output_or_a_repair_request(ai_app, api, monkeypatch):
    configure(api, api_key='dummy"quoted\\key')
    ai_app.state.engine.sync_day(iso_today())

    def handler(request):
        assert "quoted" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "phenomenon": '步数 8000 步。dummy"quoted\\key', "causes": None, "advice": None,
        })}}]})

    mock_model(monkeypatch, handler)
    result = api.post("/api/v1/ai/analyze", json={"date": iso_today()})
    assert result.status_code == 200 and "quoted" not in result.text
    assert "quoted" not in api.get("/api/v1/export").text


@pytest.mark.parametrize("base_url", ["https://model.example/v1", "https://model.example/v1/chat/completions/"])
def test_probe_and_analysis_use_the_same_canonical_url_saved_model_and_key(ai_app, api, monkeypatch, base_url):
    result = configure(api, ai_base_url=base_url, ai_model="saved-model")
    assert result["base_url"] == "https://model.example/v1"
    # 同时覆盖旧版本已存入完整端点的情况，不要求用户重新保存一次。
    storage.set_setting(ai_app.state.conn, "ai_base_url", base_url)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert body["model"] == "saved-model"
        if body["max_tokens"] == 256:
            assert set(body) == {"model", "messages", "max_tokens", "stream"}
            assert body["messages"] == [{"role": "user", "content": "请只回复 OK。"}]
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        return good_reply()

    mock_model(monkeypatch, handler)
    probe = api.post("/api/v1/ai/test").json()
    assert probe == {"ok": True, "status": 200, "detail": "连接正常", "model": "saved-model",
                     "latency_ms": probe["latency_ms"]}
    assert probe["latency_ms"] >= 0
    assert storage.count_days(ai_app.state.conn) == 0
    assert ai_app.state.conn.execute("SELECT COUNT(*) FROM ai_analyses").fetchone()[0] == 0
    ai_app.state.engine.sync_day(iso_today())
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).status_code == 200
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).json()["cached"] is True
    assert [url for url, _ in requests] == ["https://model.example/v1/chat/completions"] * 2
    assert [body["max_tokens"] for _, body in requests] == [256, 2000]


@pytest.mark.parametrize("status,expected", [
    (401, "密钥无效或已过期 —— 检查 key 是否复制完整、是否属于这个服务。"),
    (402, "额度不足 —— 这个 key 或模型没有可用额度，换 key 或补充额度。"),
    (403, "没有权限使用该模型 —— 核对模型名或账号权限。"),
    (404, "接口地址不对 —— 检查是否少填了 /v1，并核对模型名称。"),
    (429, "请求太频繁 —— 触发限流，请稍后重试。"),
    (500, "对方服务出错（HTTP 500）—— 稍后重试，反复出现请检查服务状态。"),
])
def test_probe_http_status_wording_is_actionable_and_never_echoes_provider_body(
    ai_app, api, monkeypatch, caplog, status, expected
):
    configure(api, ai_enabled=False)  # 配置阶段可测试，不要求先开启健康分析。
    mock_model(monkeypatch, lambda request: httpx.Response(status, text=f"invalid key: {KEY}"))
    with caplog.at_level(logging.DEBUG):
        response = api.post("/api/v1/ai/test")
    result = response.json()
    assert response.status_code == 200 and result["ok"] is False
    assert result["status"] == status and result["detail"] == expected
    assert KEY not in response.text and KEY not in caplog.text
    assert ("重试" in result["detail"]) == (status in (429, 500))
    assert ai_app.state.conn.execute("SELECT COUNT(*) FROM ai_egress").fetchone()[0] == 0
    assert KEY not in api.get("/api/v1/export").text


@pytest.mark.parametrize("kind,expected", [
    ("timeout", "连不上该地址（请求超时）—— 检查地址与网络；本地 Ollama 需先启动，稍后重试。"),
    ("dns_error", "域名解析失败 —— 检查地址拼写，或该域名是否需要代理。"),
    ("connection_error", "无法连接模型服务 —— 检查地址、网络及服务是否启动。"),
])
def test_probe_distinguishes_timeout_dns_and_connection_errors(ai_app, api, monkeypatch, kind, expected):
    configure(api)

    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout(KEY, request=request)
        if kind == "dns_error":
            try:
                raise socket.gaierror(socket.EAI_NONAME, KEY)
            except socket.gaierror as cause:
                raise httpx.ConnectError(KEY, request=request) from cause
        raise httpx.ConnectError(KEY, request=request)

    mock_model(monkeypatch, handler)
    response = api.post("/api/v1/ai/test")
    assert response.json()["status"] == kind
    assert response.json()["detail"] == expected
    assert response.json()["ok"] is False and KEY not in response.text


@pytest.mark.parametrize("body,expected", [
    (None, "返回内容不是 JSON —— 确认地址指向 OpenAI Chat Completions 兼容服务。"),
    ({"choices": []}, "返回结构不符合预期 —— 确认使用 OpenAI Chat Completions 兼容接口。"),
])
def test_probe_incompatible_200_responses_are_connection_success_with_warning(ai_app, api, monkeypatch, body, expected):
    configure(api)
    mock_model(monkeypatch, lambda request: httpx.Response(200, json=body) if body is not None
               else httpx.Response(200, text=f"not json: {KEY}"))
    response = api.post("/api/v1/ai/test")
    result = response.json()
    assert result["ok"] is True and result["status"] == 200
    assert result["detail"] == "连接正常" and result["warning"] == expected
    assert KEY not in response.text


def test_probe_and_generation_switch_together_after_configuration_changes(ai_app, api, monkeypatch):
    configure(api)
    configure(api, ai_base_url="https://second.example/v1/chat/completions", ai_model="second-model", api_key="second-key")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://second.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer second-key"
        assert body["model"] == "second-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}) if body["max_tokens"] == 256 else good_reply()

    mock_model(monkeypatch, handler)
    assert api.post("/api/v1/ai/test").json()["model"] == "second-model"
    ai_app.state.engine.sync_day(iso_today())
    assert api.post("/api/v1/ai/analyze", json={"date": iso_today()}).json()["model"] == "second-model"
    assert len(requests) == 2


@pytest.mark.parametrize("base_url,has_session", [
    ("https://opencode.ai/zen/go/v1", True),
    ("https://api.opencode.ai/v1", True),
    ("https://OPENCODE.AI./zen/go/v1", True),
    ("https://notopencode.ai/v1", False),
    ("https://opencode.ai.example/v1", False),
    ("https://model.example/opencode.ai/v1", False),
    ("https://api.openai.com/v1", False),
    ("https://api.deepseek.com/v1", False),
    ("http://127.0.0.1:11434/v1", False),
])
def test_session_header_is_host_scoped_and_user_agent_is_always_ours(
    ai_app, api, monkeypatch, base_url, has_session
):
    from health_by import __version__
    configure(api, ai_base_url=base_url, api_key="")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"] == f"health-by/{__version__}"
        assert "authorization" not in request.headers
        assert ("x-opencode-session" in request.headers) is has_session
        if has_session:
            assert UUID(request.headers["x-opencode-session"]).version == 4
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    mock_model(monkeypatch, handler)
    assert api.post("/api/v1/ai/test").json()["ok"] is True
    assert api.post("/api/v1/ai/test").json()["ok"] is True
    if has_session:
        assert requests[0].headers["x-opencode-session"] != requests[1].headers["x-opencode-session"]
    assert ai_app.state.conn.execute("SELECT COUNT(*) FROM ai_analyses").fetchone()[0] == 0


def test_daily_sessions_survive_new_service_and_are_isolated_by_date_model_and_host(ai_app, api, monkeypatch):
    from health_by import __version__
    from health_by.ai.service import AIService
    configure(api, ai_base_url="https://opencode.ai/zen/go/v1")
    dates = iso_range(iso_today(), 2)
    ai_app.state.engine.sync_days(dates)
    sessions = []

    def handler(request):
        sessions.append(request.headers.get("x-opencode-session"))
        return good_reply()

    mock_model(monkeypatch, handler)
    service = ai_app.state.ai
    assert service.analyze(dates[0])["cached"] is False
    assert service.analyze(dates[0])["cached"] is True
    assert len(sessions) == 1
    service = AIService(service.cfg)  # 新服务实例从数据库恢复同日期 UUID。
    service.analyze(dates[0], force=True)
    assert sessions[0] == sessions[1]
    service.analyze(dates[1])
    assert sessions[2] != sessions[0]
    api.post("/api/v1/settings/ai", json={"ai_model": "another-model"})
    service.analyze(dates[0])
    assert sessions[3] not in sessions[:3]
    api.post("/api/v1/settings/ai", json={"ai_base_url": "https://api.opencode.ai/v1"})
    service.analyze(dates[0])
    assert sessions[4] not in sessions[:4]
    api.post("/api/v1/settings/ai", json={"ai_base_url": "https://model.example/v1"})
    service.analyze(dates[0])
    assert sessions[5] is None
    rows = ai_app.state.conn.execute("SELECT session_id, user_agent FROM ai_egress ORDER BY id").fetchall()
    assert [row["session_id"] for row in rows] == sessions
    assert all(row["user_agent"] == f"health-by/{__version__}" for row in rows)
    exported = api.get("/api/v1/export").json()["ai_egress"]
    assert [row["session_id"] for row in exported] == sessions


def test_v4_upgrade_preserves_egress_history_and_adds_nullable_header_metadata(tmp_path):
    from health_by import db
    conn = db.connect(tmp_path / "upgrade.db")
    try:
        for version, sql in db.MIGRATIONS:
            if version <= 4:
                conn.executescript(sql)
        conn.execute("INSERT INTO meta VALUES ('schema_version', '4')")
        conn.execute("INSERT INTO ai_analyses (id,date,model,prompt_version,created_at,base_url,status) "
                     "VALUES (1,'2026-01-01','model','v1','now','https://model.example/v1','error')")
        conn.execute("INSERT INTO ai_egress (analysis_id,attempt,payload_keys_json,payload_keys_hash,payload_hash,"
                     "payload_shapes_json,request_hash,status,sent_at) VALUES (1,1,'[]','keys','payload','{}','request','error','now')")
        conn.commit()
        db.migrate(conn)
        db.migrate(conn)
        row = conn.execute("SELECT * FROM ai_egress").fetchone()
        assert row["payload_hash"] == "payload" and row["status"] == "error"
        assert row["session_id"] is None and row["user_agent"] is None
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    finally:
        conn.close()


@pytest.mark.parametrize("content", ["", "   ", None])
def test_probe_empty_length_limited_reply_is_success_with_warning(api, monkeypatch, content):
    configure(api)
    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 256
        assert body["messages"] == [{"role": "user", "content": "请只回复 OK。"}]
        assert "response_format" not in body
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": content}}]})
    mock_model(monkeypatch, handler)
    result = api.post("/api/v1/ai/test").json()
    assert result["ok"] is True and result["status"] == 200
    assert result["detail"] == "连接正常"
    assert result["warning"] == "连接正常，但模型没有返回内容；生成分析时若仍为空，考虑换模型。"


def test_analysis_empty_content_still_fails_validation_with_its_own_token_budget(ai_app, api, monkeypatch):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    mock_model(monkeypatch, handler)
    result = api.post("/api/v1/ai/analyze", json={"date": iso_today()}).json()
    assert result["validation"]["state"] == "failed" and result["attempts"] == 2
    assert len(requests) == 2 and all(body["max_tokens"] == 2000 for body in requests)
    assert all(body["response_format"] == {"type": "json_object"} for body in requests)


@pytest.mark.parametrize("base_url,with_key", [
    ("https://opencode.ai/zen/go/v1", False),
    ("https://model.example/v1", True),
    ("http://127.0.0.1:11434/v1", False),
])
def test_model_list_uses_saved_address_key_and_never_needs_a_selected_model(ai_app, api, monkeypatch, base_url, with_key):
    from health_by import __version__
    configure(api, ai_enabled=False, ai_base_url=base_url, ai_model="", api_key=KEY if with_key else "")
    before = storage.load_settings(ai_app.state.conn)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET" and str(request.url) == base_url + "/models"
        assert request.content == b"" and "x-opencode-session" not in request.headers
        assert request.headers["user-agent"] == f"health-by/{__version__}"
        assert request.headers.get("authorization") == (f"Bearer {KEY}" if with_key else None)
        return httpx.Response(200, json={"data": [
            {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "api": {"base_url": "https://untrusted.example"}},
            {"id": "only-id"}, {"id": "only-id"}, {"id": None},
        ]})
    mock_model(monkeypatch, handler)
    result = api.post("/api/v1/ai/models").json()
    assert result["ok"] is True and result["status"] == 200
    assert result["models"] == [{"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"},
                                {"id": "only-id", "name": "only-id"}]
    assert len(requests) == 1 and storage.load_settings(ai_app.state.conn) == before
    assert storage.count_days(ai_app.state.conn) == 0
    for table in ("ai_analyses", "ai_egress"):
        assert ai_app.state.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("kind,detail", [
    (404, "该地址没有模型列表接口，请手动填写模型名。"),
    (401, "密钥无效或已过期"),
    ("timeout", "连不上该地址"),
])
def test_model_list_failure_is_safe_and_manual_configuration_is_still_allowed(ai_app, api, monkeypatch, caplog, kind, detail):
    configure(api, ai_base_url="https://opencode.ai/zen/go/v1")
    calls = []
    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout(KEY, request=request)
        return httpx.Response(kind, text=f"upstream echoed {KEY}")
    mock_model(monkeypatch, handler)
    with caplog.at_level(logging.DEBUG):
        response = api.post("/api/v1/ai/models")
    result = response.json()
    assert result["ok"] is False and result["status"] == kind and result["models"] == []
    assert detail in result["detail"] and KEY not in response.text and KEY not in caplog.text
    assert len(calls) == 1  # 包括 OpenCode 404，也不猜路径或偷偷重试。
    assert api.post("/api/v1/settings/ai", json={"ai_model": "manually-entered"}).status_code == 200
    assert storage.load_settings(ai_app.state.conn)["ai_model"] == "manually-entered"
    assert KEY not in api.get("/api/v1/export").text


@pytest.mark.parametrize("body", [None, {}, {"data": {}}, {"data": [{"id": 123}]}])
def test_bad_model_lists_are_reported_without_echoing_upstream_values(api, monkeypatch, body):
    configure(api)
    mock_model(monkeypatch, lambda request: httpx.Response(200, json=body) if body is not None
               else httpx.Response(200, text=KEY))
    response = api.post("/api/v1/ai/models")
    assert response.json()["ok"] is False and "返回结构" in response.json()["detail"]
    assert KEY not in response.text


def test_empty_model_list_and_successful_key_echo_are_safe(api, monkeypatch):
    configure(api)
    mock_model(monkeypatch, lambda request: httpx.Response(200, json={"data": []}))
    result = api.post("/api/v1/ai/models").json()
    assert result["ok"] is True and not result["models"] and "手动填写" in result["detail"]
    mock_model(monkeypatch, lambda request: httpx.Response(200, json={"data": [
        {"id": KEY, "name": KEY}, {"id": "model", "name": f"example {KEY}"},
    ]}))
    response = api.post("/api/v1/ai/models")
    assert KEY not in response.text
    assert response.json()["models"] == [{"id": "model", "name": "example [已隐藏密钥]"}]


def test_a_small_unsupported_tail_is_visible_as_warning_without_triggering_repair(ai_app, api, monkeypatch):
    configure(api)
    snapshot = ai_app.state.engine.sync_day(iso_today())
    calls = []
    text = f'当天步数 {snapshot.activity.steps} 步。' * 3 + '今天状态不错。'
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'phenomenon': text, 'causes': None, 'advice': None})}}]})
    mock_model(monkeypatch, handler)
    result = api.post('/api/v1/ai/analyze', json={'date': iso_today()}).json()
    assert result['validation']['state'] == 'warn' and result['validation']['unsupported_sentences'] == 1
    assert result['content']['phenomenon'] == text and result['attempts'] == len(calls) == 1
    assert result['validation']['warnings'][0]['text'] == '今天状态不错。'


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_length_finish_reason_is_distinct_from_format_failure_and_survives_cache(ai_app, api, monkeypatch, repair_succeeds):
    configure(api)
    snapshot = ai_app.state.engine.sync_day(iso_today())
    calls = []
    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 2000
        calls.append(body)
        if len(calls) == 2 and repair_succeeds:
            text = json.dumps({"phenomenon": f"步数 {snapshot.activity.steps} 步。", "causes": None, "advice": None})
            reason = "stop"
        else:
            text, reason = '{"phenomenon":"步数', "length"
        return httpx.Response(200, json={"choices": [{"finish_reason": reason, "message": {"content": text}}]})
    mock_model(monkeypatch, handler)
    result = api.post('/api/v1/ai/analyze', json={'date': iso_today()}).json()
    assert len(calls) == result['attempts'] == 2
    assert result['validation']['attempt_finish_reasons'] == ['length', 'stop' if repair_succeeds else 'length']
    assert result['finish_reason'] == ('stop' if repair_succeeds else 'length')
    assert result['truncated'] is (not repair_succeeds)
    if not repair_succeeds:
        assert result['validation']['state'] == 'failed'
        assert len(result['validation']['issues']) == 1
        assert '输出被截断' in result['validation']['issues'][0]['reason']
        assert '2,000 tokens' in result['validation']['issues'][0]['reason']
        assert '三字段' not in result['validation']['issues'][0]['reason']
    cached = api.get('/api/v1/ai/analysis', params={'date': iso_today()}).json()['analysis']
    assert cached['finish_reason'] == result['finish_reason'] and cached['truncated'] == result['truncated']


def test_a_real_format_error_without_length_is_not_labeled_truncation(ai_app, api, monkeypatch):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())
    mock_model(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{
        'finish_reason': 'stop', 'message': {'content': '{bad-json'}}]}))
    result = api.post('/api/v1/ai/analyze', json={'date': iso_today()}).json()
    assert result['finish_reason'] == 'stop' and result['truncated'] is False
    assert any('三字段 JSON' in item['reason'] for item in result['validation']['issues'])
    assert not any('截断' in item['reason'] for item in result['validation']['issues'])


def test_null_content_with_length_still_retains_the_truncation_reason(ai_app, api, monkeypatch):
    configure(api)
    ai_app.state.engine.sync_day(iso_today())
    mock_model(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{
        'finish_reason': 'length', 'message': {'content': None}}]}))
    result = api.post('/api/v1/ai/analyze', json={'date': iso_today()}).json()
    assert result['finish_reason'] == 'length' and result['truncated'] is True
    assert '截断' in result['validation']['issues'][0]['reason']


def test_schema_version_partitions_cache_and_keeps_the_legacy_record(ai_app, api, monkeypatch):
    from health_by.ai.service import payload_audit

    configure(api)
    day = "2024-02-28"
    ai_app.state.engine.sync_days(iso_range(day, 14))
    content = {"phenomenon": "合成旧记录 20 ms", "causes": None, "advice": None}
    audit = payload_audit({"date": "D-0", "day": {"heart": {"hrv_ms": 20}}})
    with ai_app.state.conn as conn:
        old_id = conn.execute(
            "INSERT INTO ai_analyses (date,start_date,end_date,model,prompt_version,content,raw_content,"
            "validation_json,created_at,base_url,status,payload_schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (day,day,day,"test-model",prompt.PROMPT_VERSION,json.dumps(content),"LEGACY-TEXT",
             '{"state":"ok"}',"2024-03-01T00:00:00Z","https://model.example/v1","ok","daily-v1"),
        ).lastrowid
        conn.execute(
            "INSERT INTO ai_egress (analysis_id,attempt,payload_keys_json,payload_keys_hash,payload_hash,"
            "payload_shapes_json,request_hash,status,sent_at,payload_schema_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (old_id,1,json.dumps(audit["payload_keys"]),audit["payload_keys_hash"],audit["payload_hash"],
             json.dumps(audit["payload_shapes"]),"synthetic-request","ok","2024-03-01T00:00:00Z","daily-v1"),
        )
    before = dict(ai_app.state.conn.execute("SELECT * FROM ai_analyses WHERE id=?", (old_id,)).fetchone())
    calls = []
    mock_model(monkeypatch, lambda request: (calls.append(request) or good_reply()))
    assert api.get("/api/v1/ai/analysis", params={"date": day}).json()["analysis"] is None
    assert calls == []
    result = api.post("/api/v1/ai/analyze", json={"date": day}).json()
    assert result["cached"] is False and result["payload_schema_version"] == "daily-v2"
    assert len(calls) == 1
    cached = api.get("/api/v1/ai/analysis", params={"date": day}).json()["analysis"]
    assert cached["cached"] is True and cached["payload_schema_version"] == "daily-v2"
    assert api.post("/api/v1/ai/analyze", json={"date": day}).json()["cached"] is True
    assert len(calls) == 1
    assert dict(ai_app.state.conn.execute("SELECT * FROM ai_analyses WHERE id=?", (old_id,)).fetchone()) == before
    exported = api.get("/api/v1/export").json()
    assert {row["payload_schema_version"] for row in exported["ai_analyses"]} == {"daily-v1", "daily-v2"}
    assert {row["payload_schema_version"] for row in exported["ai_egress"]} == {"daily-v1", "daily-v2"}


def test_daily_both_actual_requests_use_the_same_dual_payload_and_schema(ai_app, api, monkeypatch):
    from health_by.models import ActivityDay, DaySnapshot, HeartDay
    from health_by.providers.base import RawDay
    from health_by.util import shift_iso

    configure(api)
    day = "2024-02-28"
    for index in range(7):
        selected = shift_iso(day, index - 6)
        snapshot = DaySnapshot(selected, "synthetic", "2024-03-01T00:00:00Z",
                               activity=ActivityDay(steps=0), heart=HeartDay(hrv_ms=20 if index < 6 else 40))
        storage.store_day(ai_app.state.conn, snapshot, RawDay(selected, "synthetic", snapshot.generated_at, {}))
    payloads = []
    def handler(request):
        messages = json.loads(request.content)["messages"]
        payload = json.loads(messages[1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        payloads.append(payload)
        text = "没有依据的感想。" if len(payloads) == 1 else (
            f"当日 HRV {payload['day']['heart']['hrv_ms_today']} ms；"
            f"7 日均值 {payload['day']['heart']['hrv_ms_7d']} ms。"
        )
        return httpx.Response(200, json={"choices":[{"message":{"content":json.dumps({
            "phenomenon":text, "causes":"当日 HRV 有记录。" if len(payloads) == 2 else None, "advice":None,
        }, ensure_ascii=False)}, "finish_reason":"stop"}]})
    mock_model(monkeypatch, handler)
    response = api.post("/api/v1/ai/analyze", json={"date":day})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["attempts"] == 2 and result["validation"]["state"] in {"ok", "warn"}
    assert payloads[0] == payloads[1]
    assert payloads[0]["day"]["heart"]["hrv_ms_today"] == 40
    assert payloads[0]["day"]["heart"]["hrv_ms_7d"] == 22.9
    assert payloads[0]["day"]["activity"]["steps_today"] == 0
    assert all(not key.endswith("_7d") for key in payloads[0]["day"]["activity"])
    rows = ai_app.state.conn.execute("SELECT * FROM ai_egress ORDER BY attempt").fetchall()
    assert [row["payload_schema_version"] for row in rows] == ["daily-v2", "daily-v2"]
    assert rows[0]["payload_keys_hash"] == rows[1]["payload_keys_hash"]
    assert rows[0]["payload_hash"] == rows[1]["payload_hash"]
