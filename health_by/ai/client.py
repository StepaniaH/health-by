"""最小 OpenAI Chat Completions 兼容客户端，不记录请求或上游错误正文。"""

from __future__ import annotations

from health_by.i18n import t

import json
import socket
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .. import __version__

ANALYSIS_MAX_TOKENS = 2000

USER_AGENT = f"health-by/{__version__}"

# 配置诊断文案的唯一来源；连接测试与正式调用共用，不回传上游正文。
AI_ERROR_TEXT = {
    200: t('server.ai.client.labels.da0bfef25'),
    400: t('server.ai.client.labels.e91229684'),
    401: t('server.ai.client.labels.b4f1f96a9'),
    402: t('server.ai.client.labels.8dfceb9d9'),
    403: t('server.ai.client.labels.7119608ee'),
    404: t('server.ai.client.labels.ea0e1c9bc'),
    429: t('server.ai.client.labels.7d01ca9eb'),
    "server_error": t('server.ai.client.labels.46f52df21'),
    "unknown_http": t('server.ai.client.labels.0565c7234'),
    "timeout": t('server.ai.client.labels.529e70721'),
    "dns_error": t('server.ai.client.labels.927a19315'),
    "connection_error": t('server.ai.client.labels.fd9b116e4'),
    "invalid_url": t('server.ai.client.labels.b2973a096'),
    "invalid_json": t('server.ai.client.labels.df830925b'),
    "invalid_response": t('server.ai.client.labels.50d8307c9'),
    "empty_content_warning": t('server.ai.client.labels.b8f7b46d3'),
    "missing_model": t('server.ai.client.labels.de26b8165'),
    "models_404": t('server.ai.client.labels.6a80f6450'),
    "models_invalid": t('server.ai.client.labels.230885c7f'),
    "models_empty": t('server.ai.client.labels.b49eadfe7'),
    "models_ok": t('server.ai.client.labels.24e6c176f'),
}


def http_error_text(status: int) -> str:
    key = status if status in AI_ERROR_TEXT else ("server_error" if status >= 500 else "unknown_http")
    return AI_ERROR_TEXT[key].format(status=status)


def _is_dns_error(exc: BaseException) -> bool:
    pending = [exc]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
    return False


class AIError(Exception):
    def __init__(self, message: str, status: int = 400, *, kind: str | None = None):
        super().__init__(message)
        self.status = status
        self.kind = kind


def validate_base_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        valid = parts.scheme in ("http", "https") and parts.hostname and parts.port != 0
    except ValueError:
        valid = False
    if not valid or parts.username or parts.password or parts.query or parts.fragment:
        raise AIError(t('server.ai.client.validate_base_url.ffde5272c'))
    normalized = value.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[:-len("/chat/completions")]
    return normalized


def _redact(value, key: str):
    if isinstance(value, str):
        return value.replace(key, t('server.ai.client._redact.ac4d2f3ae'))
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


class AIClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", *, session_id: str | None = None):
        self.base_url = validate_base_url(base_url)
        self.model = model
        self._api_key = api_key
        host = urlsplit(self.base_url).hostname.lower().rstrip(".")
        self.is_opencode = host == "opencode.ai" or host.endswith(".opencode.ai")
        self.session_id = (session_id or str(uuid4())) if self.is_opencode else None

    def _send(self, method: str, url: str, *, timeout: float, body=None, session: bool = False) -> httpx.Response:
        headers = {"accept": "application/json", "user-agent": USER_AGENT}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        if session and self.session_id:
            headers["x-opencode-session"] = self.session_id
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=10), trust_env=False, follow_redirects=False) as http:
                return http.request(method, url, headers=headers, json=body)
        except httpx.TimeoutException:
            raise AIError(AI_ERROR_TEXT["timeout"], 504, kind="timeout") from None
        except httpx.ConnectError as exc:
            kind = "dns_error" if _is_dns_error(exc) else "connection_error"
            raise AIError(AI_ERROR_TEXT[kind], 502, kind=kind) from None
        except httpx.InvalidURL:
            raise AIError(AI_ERROR_TEXT["invalid_url"], 400, kind="invalid_url") from None
        except httpx.HTTPError:
            raise AIError(AI_ERROR_TEXT["connection_error"], 502, kind="connection_error") from None

    def _request(self, messages: list[dict], *, max_tokens: int, json_mode: bool, timeout: float) -> httpx.Response:
        body = {"model": self.model, "messages": messages, "stream": False, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return self._send("POST", self.base_url + "/chat/completions", timeout=timeout, body=body, session=True)

    def list_models(self) -> dict:
        status: int | str = 0
        try:
            response = self._send("GET", self.base_url + "/models", timeout=20)
            status = response.status_code
            if status != 200:
                detail = AI_ERROR_TEXT["models_404"] if status == 404 else http_error_text(status)
                return {"ok": False, "status": status, "detail": detail, "models": []}
            try:
                data = response.json()["data"]
                if not isinstance(data, list):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                raise AIError(AI_ERROR_TEXT["models_invalid"]) from None
            models = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id")
                if (not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200
                        or any(ord(char) < 32 for char in model_id)
                        or (self._api_key and self._api_key in model_id)):
                    continue
                name = item.get("name")
                name = name if isinstance(name, str) and name.strip() else model_id
                if self._api_key:
                    name = name.replace(self._api_key, t('server.ai.client.list_models.ac4d2f3ae'))
                models.setdefault(model_id, {"id": model_id, "name": name[:200]})
            if data and not models:
                raise AIError(AI_ERROR_TEXT["models_invalid"])
            return {"ok": True, "status": 200, "detail": AI_ERROR_TEXT["models_ok" if models else "models_empty"],
                    "models": list(models.values())}
        except AIError as exc:
            return {"ok": False, "status": exc.kind or status or exc.status, "detail": str(exc), "models": []}

    @staticmethod
    def _response_parts(response: httpx.Response, *, allow_empty: bool = False) -> tuple[str, dict]:
        try:
            result = response.json()
        except ValueError:
            raise AIError(AI_ERROR_TEXT["invalid_json"], 502) from None
        try:
            text = result["choices"][0]["message"]["content"]
            if text is None and allow_empty:
                text = ""
            if not isinstance(text, str):
                raise TypeError
            return text, result
        except (KeyError, IndexError, TypeError):
            raise AIError(AI_ERROR_TEXT["invalid_response"], 502) from None

    def test_connection(self) -> dict:
        started = time.perf_counter()
        status: int | str = 0
        warning = None
        try:
            response = self._request(
                [{"role": "user", "content": t('server.ai.client.test_connection.d8754faa6')}], max_tokens=256, json_mode=False, timeout=20,
            )
            status = response.status_code
            if status != 200:
                ok, detail = False, http_error_text(status)
            else:
                text, _ = self._response_parts(response, allow_empty=True)
                ok, detail = True, AI_ERROR_TEXT[200]
                if not text.strip():
                    warning = AI_ERROR_TEXT["empty_content_warning"]
        except AIError as exc:
            if status == 200:
                ok, detail, warning = True, AI_ERROR_TEXT[200], str(exc)
            else:
                ok, status, detail = False, exc.kind or status or exc.status, str(exc)
        return {"ok": ok, "status": status, "detail": detail, "model": self.model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                **({"warning": warning} if warning else {})}

    def complete(self, messages: list[dict]) -> tuple[str, dict, str | None]:
        response = self._request(messages, max_tokens=ANALYSIS_MAX_TOKENS, json_mode=True, timeout=120)
        if response.status_code != 200:
            # 不回传 provider 的错误正文：它可能回显 Authorization 或请求内容。
            raise AIError(http_error_text(response.status_code), 429 if response.status_code == 429 else 502)
        try:
            text, result = self._response_parts(response, allow_empty=True)
            if self._api_key:
                try:
                    text = json.dumps(_redact(json.loads(text), self._api_key), ensure_ascii=False)
                except ValueError:
                    text = text.replace(self._api_key, t('server.ai.client.complete.ac4d2f3ae'))
            raw_usage = result.get("usage") or {}
            usage = {
                key: value if isinstance(value := raw_usage.get(key), int) and not isinstance(value, bool) and value >= 0 else None
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            reason = result["choices"][0].get("finish_reason")
            reason = reason if reason in ("stop", "length", "tool_calls", "function_call", "content_filter") else None
            return text, usage, reason
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AIError(AI_ERROR_TEXT["invalid_response"], 502) from None
