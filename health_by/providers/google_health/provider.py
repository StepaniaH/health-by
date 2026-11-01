"""Google Health provider：OAuth + 31 类数据抓取 + 翻译。"""

from __future__ import annotations

from health_by.i18n import t


from ...config import env_value
from ...util import shift_iso, utc_now_iso
from ..base import Provider, ProviderAuthError, ProviderError, RawDay
from . import client as gh_client
from .registry import ALL_JOBS, ECG_DAYS, TREND_DAYS, Job
from .translate import translate_payload


class GoogleHealthProvider(Provider):
    name = "google-health"
    display_name = "Google Health"
    auth_kind = "oauth"

    def __init__(self, pace_ms: int | None = None):
        if pace_ms is None:
            pace_ms = int(env_value("PACE_MS", gh_client.DEFAULT_PACE_MS))
        self._pace_ms = max(0, pace_ms)
        # 多天并发时所有线程共享同一节奏，整体仍保持在 <5 req/s
        self._pacer = gh_client.Pacer(self._pace_ms)

    # ---------- OAuth ----------

    def new_pkce(self) -> tuple[str, str]:
        return gh_client.create_pkce()

    def auth_status(self, credentials: dict | None) -> dict:
        credentials = credentials or {}
        tokens = credentials.get("tokens") or {}
        return {
            "configured": bool(credentials.get("client_id") and credentials.get("client_secret")),
            "connected": bool(tokens.get("refresh_token") or tokens.get("access_token")),
            "needs_reconnect": bool(credentials.get("needs_reconnect")),
            "client_id": credentials.get("client_id") or "",
            "redirect_uri": credentials.get("redirect_uri") or "",
        }

    def build_auth_url(self, *, credentials: dict, redirect_uri: str, state: str, code_challenge: str) -> str:
        return gh_client.build_auth_url(credentials["client_id"], redirect_uri, state, code_challenge)

    def exchange_code(
        self, *, credentials: dict, redirect_uri: str, code: str, code_verifier: str, http
    ) -> dict:
        return gh_client.exchange_code(
            http,
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )

    def refresh(self, *, credentials: dict, http) -> dict:
        tokens = credentials.get("tokens") or {}
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise ProviderAuthError(t('server.providers.google_health.provider.refresh.e749febd6'))
        return gh_client.refresh_tokens(
            http,
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
            refresh_token=refresh_token,
        )

    def revoke(self, *, credentials: dict, http) -> None:
        tokens = credentials.get("tokens") or {}
        token = tokens.get("refresh_token") or tokens.get("access_token")
        if not token:
            return
        try:
            gh_client.revoke_token(http, token)
        except Exception:  # noqa: BLE001 — 吊销尽力而为，失败不影响本地断开
            pass

    # ---------- 数据 ----------

    def fetch_raw(
        self, date: str, *, credentials: dict | None = None, http=None, on_progress=None
    ) -> RawDay:
        access_token = ((credentials or {}).get("tokens") or {}).get("access_token")
        if not access_token:
            raise ProviderAuthError(t('server.providers.google_health.provider.fetch_raw.6629cd2d4'))
        client = gh_client.GoogleHealthClient(http, pace_ms=self._pace_ms, pacer=self._pacer)

        trend_start = shift_iso(date, -(TREND_DAYS - 1))
        day_after = shift_iso(date, 1)
        ecg_start = shift_iso(date, -(ECG_DAYS - 1))

        payload: dict = {}
        errors: list[dict] = []
        total = len(ALL_JOBS)
        for completed, job in enumerate(ALL_JOBS, start=1):
            try:
                payload[job.key] = self._fetch_job(client, access_token, job, date, trend_start, day_after, ecg_start)
            except ProviderAuthError:
                raise
            except ProviderError as exc:
                errors.append({"key": job.key, "message": str(exc), "status": exc.status})
            if on_progress is not None:
                on_progress(completed, total, job.key)
        return RawDay(
            date=date,
            source=self.name,
            generated_at=utc_now_iso(),
            payload=payload,
            errors=errors,
            stats={"total": total, "succeeded": total - len(errors)},
        )

    def _fetch_job(
        self,
        client: gh_client.GoogleHealthClient,
        token: str,
        job: Job,
        date: str,
        trend_start: str,
        day_after: str,
        ecg_start: str,
    ) -> dict:
        if job.kind == "meta":
            return client.get(job.path, token, params=job.params or None)
        if job.kind == "rollup":
            return client.daily_rollup(token, job.dtype, trend_start, day_after)
        if job.kind == "intraday":
            return client.list_data(token, job.dtype, job.record, date, day_after, family=job.family, op="list")
        window_start = ecg_start if job.window == "ecg" else trend_start
        return client.list_data(
            token,
            job.dtype,
            job.record,
            window_start,
            day_after,
            family=job.family,
            op=job.op,
            page_size=job.page_size,
        )

    def translate(self, raw: RawDay):
        return translate_payload(raw)
