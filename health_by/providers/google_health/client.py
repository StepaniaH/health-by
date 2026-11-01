"""Google Health API v4 客户端：OAuth、请求节流/重试、数据读取。

已知怪癖（调研确认，见 docs/data-coverage.md）：
- 出站限速 <5 req/s：统一走 Pacer 节流（默认 225ms 间距）；
- dailyRollUp 的 civil 区间结尾要用"当日 23:59:59"（文档模式与实现不符）；
- reconcile 会自动跨数据源合并去重（官方"对账流"）；
- 401 一律视为授权失效（需要用户重新连接）。
"""

from __future__ import annotations

from health_by.i18n import t

import base64
import hashlib
import os
import random
import threading
import time
from typing import Any, Callable

import httpx

from ..base import ProviderAuthError, ProviderError
from ...util import shift_iso

API_BASE = "https://health.googleapis.com/v4"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = [
    "openid",
    "profile",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
    "https://www.googleapis.com/auth/googlehealth.irn.readonly",
    "https://www.googleapis.com/auth/googlehealth.location.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]

DEFAULT_PACE_MS = 225
MAX_RETRIES = 2
RETRY_CAP_SECONDS = 30
PAGINATION_GUARD = 100
# 503/504/5xx 与网络层抖动都是暂时性的，一次失败不代表这天没数据。
# 429 已有单独处理（尊重 retry-after），这里补上其余可重试情形。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_BASE = 1.1
RETRY_JITTER_SECONDS = 0.25


# ---------- OAuth ----------


def create_pkce() -> tuple[str, str]:
    """返回 (verifier, challenge)，RFC 7636 S256。"""
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{httpx.QueryParams(params)}"


def _token_request(http: httpx.Client, parameters: dict[str, str]) -> dict:
    response = http.post(
        TOKEN_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data=parameters,
    )
    payload: dict[str, Any] = {}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        error = payload.get("error")
        if error in ("invalid_grant", "invalid_client", "unauthorized_client"):
            raise ProviderAuthError(
                t('server.providers.google_health.client._token_request.18783d2e1', error=error),
            )
        raise ProviderError(
            payload.get("error_description") or error or t('server.providers.google_health.client._token_request.246361521', p1=response.status_code),
            status=response.status_code,
        )
    if isinstance(payload.get("expires_in"), (int, float)):
        payload["expires_at"] = time.time() + float(payload["expires_in"])
    return payload


def exchange_code(
    http: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict:
    return _token_request(
        http,
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
    )


def refresh_tokens(http: httpx.Client, *, client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _token_request(
        http,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


def revoke_token(http: httpx.Client, token: str) -> None:
    response = http.post(
        REVOKE_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={"token": token},
    )
    if response.status_code >= 400:
        raise ProviderError(t('server.providers.google_health.client.revoke_token.20010e4b0', p1=response.status_code), status=response.status_code)


# ---------- 请求节流 ----------


class Pacer:
    """多任务共享的最小请求间隔（出站限速 <5 req/s）。"""

    def __init__(self, min_interval_ms: int):
        self._lock = threading.Lock()
        self.min_interval = max(0, min_interval_ms) / 1000
        self._next_slot = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


# ---------- 过滤器 ----------


def build_filter(dtype: str, record: str, start: str, end: str) -> str:
    field = dtype.replace("-", "_")
    if record == "daily":
        return f'{field}.date >= "{start}" AND {field}.date < "{end}"'
    if record == "sleep":
        return f'sleep.interval.civil_end_time >= "{start}" AND sleep.interval.civil_end_time < "{end}"'
    if record == "ecg":
        return f'electrocardiogram.interval.start_time >= "{start}T00:00:00Z"'
    if record == "sample":
        return f'{field}.sample_time.civil_time >= "{start}" AND {field}.sample_time.civil_time < "{end}"'
    return f'{field}.interval.civil_start_time >= "{start}" AND {field}.interval.civil_start_time < "{end}"'


def civil_datetime(date: str, *, end_of_day: bool = False) -> dict:
    year, month, day = (int(part) for part in date.split("-"))
    time_of_day = (
        {"hours": 23, "minutes": 59, "seconds": 59, "nanos": 0}
        if end_of_day
        else {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0}
    )
    return {"date": {"year": year, "month": month, "day": day}, "time": time_of_day}


# ---------- 客户端 ----------


class GoogleHealthClient:
    def __init__(
        self,
        http: httpx.Client,
        *,
        pace_ms: int = DEFAULT_PACE_MS,
        pacer: Pacer | None = None,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] | None = None,
    ):
        self.http = http
        self.pacer = pacer if pacer is not None else Pacer(pace_ms)
        self.max_retries = max_retries
        self._sleep = sleep or time.sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        target = url if url.startswith("http") else f"{API_BASE}{url}"
        attempt = 0
        while True:
            self.pacer.wait()
            try:
                response = self.http.request(
                    method,
                    target,
                    params=params,
                    json=json,
                    headers={"authorization": f"Bearer {token}", "accept": "application/json"},
                )
            except httpx.TransportError as exc:
                # 连接被重置 / 超时 / DNS 抖动：这类失败下一毫秒可能就好了，
                # 不该直接判成"这天没数据"。
                if attempt < self.max_retries:
                    self._sleep(self._backoff_seconds(attempt))
                    attempt += 1
                    continue
                raise ProviderError(t('server.providers.google_health.client.request.cb14de50f', p1=type(exc).__name__)) from exc
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit() and int(retry_after) > 0:
                    delay = min(RETRY_CAP_SECONDS, int(retry_after))
                else:
                    delay = self._backoff_seconds(attempt)
                self._sleep(delay)
                attempt += 1
                continue
            break
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code == 401:
            raise ProviderAuthError(t('server.providers.google_health.client.request.7b0480e02'))
        if response.status_code >= 400:
            message = None
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                message = error.get("message")
            raise ProviderError(
                message or t('server.providers.google_health.client.request.7698647ea', p1=response.status_code),
                status=response.status_code,
            )
        return payload if isinstance(payload, dict) else {}

    def _backoff_seconds(self, attempt: int) -> float:
        """指数退避 + 抖动：同时重试的 31 个 job 不要挤在同一毫秒重发。"""
        base = RETRY_BACKOFF_BASE * (2**attempt)
        jitter = random.uniform(0, RETRY_JITTER_SECONDS)
        return min(RETRY_CAP_SECONDS, base + jitter)

    def get(self, path: str, token: str, params: dict | None = None) -> dict:
        return self.request("GET", path, token=token, params=params)

    def post(self, path: str, token: str, json: dict | None = None) -> dict:
        return self.request("POST", path, token=token, json=json)

    def list_data(
        self,
        token: str,
        dtype: str,
        record: str,
        start: str,
        end: str,
        *,
        family: str = "all-sources",
        op: str = "reconcile",
        page_size: int | None = None,
    ) -> dict:
        effective_page_size = page_size or (25 if dtype in ("sleep", "exercise") else 10000)
        endpoint = "dataPoints:reconcile" if op == "reconcile" else "dataPoints"
        merged: dict[str, Any] = {"dataPoints": []}
        page_token = ""
        pages = 0
        while True:
            params: dict[str, str] = {
                "filter": build_filter(dtype, record, start, end),
                "pageSize": str(effective_page_size),
            }
            if op == "reconcile":
                params["dataSourceFamily"] = f"users/me/dataSourceFamilies/{family}"
            if page_token:
                params["pageToken"] = page_token
            page = self.get(f"/users/me/dataTypes/{dtype}/{endpoint}", token, params=params)
            merged["dataPoints"].extend(page.get("dataPoints") or [])
            page_token = str(page.get("nextPageToken") or "")
            pages += 1
            if pages >= PAGINATION_GUARD and page_token:
                raise ProviderError(t('server.providers.google_health.client.list_data.73e416e2c', dtype=dtype))
            if not page_token:
                break
        return merged

    def daily_rollup(self, token: str, dtype: str, start: str, end: str) -> dict:
        """日汇总；注意 v4 实测要求区间结尾用当日 23:59:59。"""
        return self.post(
            f"/users/me/dataTypes/{dtype}/dataPoints:dailyRollUp",
            token,
            json={
                "range": {
                    "start": civil_datetime(start),
                    "end": civil_datetime(shift_iso(end, -1), end_of_day=True),
                },
                "windowSizeDays": 1,
            },
        )
