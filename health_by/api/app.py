"""FastAPI 应用工厂：JSON API + OAuth 路由 + 静态页面。

约定：所有处理器都是同步函数（FastAPI 线程池执行），共用同一 SQLite 连接
（连接已 check_same_thread=False，写入由 Engine 单飞锁与 SQLite 事务保护）。
"""

from __future__ import annotations

from health_by.i18n import t

import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date as date_type, datetime, timedelta, timezone
from urllib.parse import quote

from functools import wraps
from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from health_by.i18n import localize, using_language, language
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, analysis, insights as insights_module, metrics, reliability, storage
from ..ai.client import AIError
from ..ai.service import AIService
from ..config import Config
from ..coverage import index_raw_dates, raw_coverage
from ..db import connect, migrate
from ..engine import Engine
from ..insights import build_insights
from ..providers import available_providers, get_provider
from ..providers.base import ProviderError
from ..util import iso_range, iso_today, shift_iso, utc_now_iso

class LocalizedRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        endpoint = kwargs.get("endpoint")
        if endpoint is not None:
            @wraps(endpoint)
            def translated(*args, **kwargs):
                return localize(endpoint(*args, **kwargs))
            kwargs["endpoint"] = translated
        super().__init__(*args, **kwargs)


# 趋势窗口的唯一权威定义在 insights 层（它需要回显给前端），这里只引用。
SUMMARY_TREND_DAYS = insights_module.SUMMARY_TREND_DAYS
OAUTH_STATE_TTL_SECONDS = 15 * 60
SYNC_MAX_DAYS = 90
CONFIRM_TOKEN = "DELETE ALL DATA"


def _validate_date(value: str) -> str:
    try:
        return date_type.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=t('server.api.app._validate_date.5cd941f20', value=value)) from exc


def _dates_between(start: str, end: str) -> list[str]:
    first = date_type.fromisoformat(start)
    last = date_type.fromisoformat(end)
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def _derive_activity(day: dict) -> dict:
    """把派生字段补进日快照（读时算，不写回存档）。

    派生值不落库的理由：落库会在"加字段那天"给所有历史数据留一个永久空洞
    （本次实测：92 天存档里新字段恒为 null），而且口径会被写那一刻的代码固化。
    """
    activity = day.get("activity")
    if not isinstance(activity, dict):
        return day
    light = activity.get("light_active_minutes")
    moderate = activity.get("moderate_active_minutes")
    vigorous = activity.get("vigorous_active_minutes")
    if light is None and moderate is None and vigorous is None:
        return day
    return {
        **day,
        "activity": {
            **activity,
            "total_active_minutes": metrics.total_active_minutes(light, moderate, vigorous),
        },
    }


def create_app(cfg: Config) -> FastAPI:
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    migrate(conn)
    engine = Engine(cfg, conn)
    ai_service = AIService(cfg)
    default_redirect_uri = f"http://127.0.0.1:{cfg.port}/oauth/callback"

    coverage_cache = None
    coverage_signature = None
    coverage_lock = threading.Lock()

    def coverage_snapshot() -> dict:
        # 原始留档可能很大：同一应用内只在数据库变化后重扫。
        # total_changes 覆盖本连接写入；data_version 覆盖同步引擎等其它连接的提交。
        nonlocal coverage_cache, coverage_signature
        with coverage_lock:
            if conn.in_transaction:
                # 不缓存尚未提交的快照；回滚本身不会增加 total_changes。
                coverage_signature = None
                return raw_coverage(conn)
            signature = (conn.total_changes, conn.execute("PRAGMA data_version").fetchone()[0])
            if coverage_cache is None or signature != coverage_signature:
                coverage_cache = raw_coverage(conn)
                coverage_signature = signature
            return coverage_cache

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 覆盖度索引在启动时补建一次：解析原始载荷很慢，不能让第一个请求来承担。
        index_raw_dates(conn)
        stop_event = threading.Event()
        settings = storage.load_settings(conn)
        if int(settings.get("sync_interval_minutes") or 0) > 0:
            threading.Thread(
                target=engine.auto_sync_loop, args=(stop_event,), daemon=True
            ).start()
        yield
        stop_event.set()
        engine.shutdown()
        engine.close()
        conn.close()

    app = FastAPI(title="Health By", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.router.route_class = LocalizedRoute

    @app.middleware("http")
    async def select_language(request: Request, call_next):
        selected = language(request.query_params.get("lang") or request.headers.get("accept-language", ""))
        with using_language(selected):
            response = await call_next(request)
        response.headers["Content-Language"] = selected
        response.headers["Vary"] = "Accept-Language"
        return response

    @app.exception_handler(HTTPException)
    async def localized_http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": localize(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    app.state.cfg = cfg
    app.state.conn = conn
    app.state.engine = engine
    app.state.ai = ai_service

    @app.exception_handler(AIError)
    async def ai_error_handler(request: Request, exc: AIError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    # ---------- 状态 ----------

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "time": utc_now_iso()}

    @app.get("/api/v1/status")
    def status() -> dict:
        settings = storage.load_settings(conn)
        last_run = next(iter(storage.list_sync_runs(conn, limit=1)), None)
        return {
            "version": __version__,
            "provider": engine.auth_status(),
            "goals": settings.get("goals"),
            "sync_interval_minutes": settings.get("sync_interval_minutes"),
            "data_dir": str(cfg.data_dir),
            "days_stored": storage.count_days(conn),
            "latest_date": storage.latest_day_date(conn),
            "today": iso_today(),
            "last_sync_run": last_run,
            "sync": engine.status(),
        }

    # ---------- 数据 ----------

    @app.get("/api/v1/coverage")
    def coverage() -> dict:
        return coverage_snapshot()

    @app.get("/api/v1/summary")
    def summary(date: str | None = None) -> dict:
        selected = _validate_date(date) if date else (storage.latest_day_date(conn) or iso_today())
        day = storage.get_day(conn, selected)
        if day is None:
            raise HTTPException(status_code=404, detail=t('server.api.app.summary.88f07f765', selected=selected))
        day = _derive_activity(day)
        day = {**day, "coverage": {**(day.get("coverage") or {}), **coverage_snapshot()}}
        settings = storage.load_settings(conn)
        # 分析需要选中日之前的完整基线窗口；图表仍保留原来的短窗口。
        analysis_days = max(SUMMARY_TREND_DAYS, metrics.BASELINE_WINDOW_DAYS + 1)
        analysis_trends = storage.get_trends(conn, selected, analysis_days)
        trend_start = shift_iso(selected, -(SUMMARY_TREND_DAYS - 1))
        trends = [point for point in analysis_trends if point["date"] >= trend_start]
        history = storage.get_day_window(conn, selected, reliability.WINDOW_DAYS)
        readings = [(point["date"], {key: metrics.to_float(value) for key, value in analysis.comparison_values(point).items()})
                    for point in history]
        current = {key: metrics.to_float(value) for key, value in analysis.comparison_values(day).items()}
        assessment = reliability.assess(readings, selected, current)
        result_analysis = analysis.build_analysis(day, analysis_trends, settings.get("goals") or {},
                                                  display_values=assessment["display_values"], display_modes=assessment["display_mode"])
        result_analysis.update(assessment)
        return {
            "date": selected,
            "day": day,
            "trends": trends,
            "goals": settings.get("goals") or {},
            "analysis": result_analysis,
        }

    @app.get("/api/v1/dates")
    def dates() -> dict:
        return {"dates": storage.available_dates(conn), "today": iso_today()}

    @app.get("/api/v1/insights")
    def insights(date: str | None = None, days: int = 30, include_hidden: int = 0) -> dict:
        selected = _validate_date(date) if date else (storage.latest_day_date(conn) or iso_today())
        window = max(7, min(90, days))
        window_days = storage.get_day_window(conn, selected, window)
        settings = storage.load_settings(conn)
        # `include_hidden` 只是排查用的开关（`?include_hidden=1`），默认关闭。
        # 是否展示未经校正的结论由 insights 层决定，这里只做透传，不在 API 层再判一次，
        # 避免"两处各自过滤"。
        return build_insights(
            window_days,
            settings.get("goals") or {},
            end_date=selected,
            window_days=window,
            include_hidden=bool(include_hidden),
        )

    # ---------- AI 解读（只有 POST 会调用模型） ----------

    @app.post("/api/v1/ai/models")
    def ai_models() -> dict:
        return ai_service.list_models()

    @app.post("/api/v1/ai/test")
    def ai_test() -> dict:
        return ai_service.test_connection()

    @app.get("/api/v1/ai/analysis")
    def ai_analysis(date: str | None = None, start: str | None = None, end: str | None = None) -> dict:
        return ai_service.get(date, start=start, end=end)

    @app.post("/api/v1/ai/analyze")
    def ai_analyze(payload: dict) -> dict:
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise AIError(t('server.api.app.ai_analyze.6268f8803'))
        return ai_service.analyze(payload.get("date"), start=payload.get("start"), end=payload.get("end"), force=force)

    @app.post("/api/v1/settings/ai")
    def ai_settings(payload: dict) -> dict:
        return ai_service.configure(conn, payload)

    # ---------- 同步 ----------

    @app.post("/api/v1/sync")
    def sync(payload: dict | None = None) -> dict:
        body = payload or {}
        dates: list[str] = []
        if body.get("dates"):
            raw_dates = body.get("dates")
            if not isinstance(raw_dates, list):
                raise HTTPException(status_code=400, detail=t('server.api.app.sync.60bba9b6e'))
            dates = [_validate_date(str(item)) for item in raw_dates]
        elif body.get("start") or body.get("end"):
            start = _validate_date(str(body.get("start") or body.get("end")))
            end = _validate_date(str(body.get("end") or body.get("start")))
            if start > end:
                start, end = end, start
            dates = _dates_between(start, end)
        elif body.get("date"):
            dates = [_validate_date(str(body["date"]))]
        elif int(body.get("days") or 0) > 0:
            days = int(body["days"])
            if days > SYNC_MAX_DAYS:
                raise HTTPException(status_code=400, detail=t('server.api.app.sync.00b5cfc7d', SYNC_MAX_DAYS=SYNC_MAX_DAYS))
            dates = iso_range(iso_today(), days)
        else:
            dates = [iso_today()]
        if len(dates) > SYNC_MAX_DAYS:
            raise HTTPException(status_code=400, detail=t('server.api.app.sync.00b5cfc7d', SYNC_MAX_DAYS=SYNC_MAX_DAYS))
        job = engine.start_background(dates)
        return {
            "started": True,
            "dates": dates,
            "outstanding": job["outstanding"],
            "added": job["added"],
            "workers": job["workers"],
        }

    @app.get("/api/v1/sync/status")
    def sync_status() -> dict:
        return engine.status()

    @app.get("/api/v1/sync/runs")
    def sync_runs(limit: int = 10) -> dict:
        return {"runs": storage.list_sync_runs(conn, limit=max(1, min(50, limit)))}

    # ---------- 设置 ----------

    def settings_payload() -> dict:
        settings = storage.load_settings(conn)
        credentials = engine.credentials_for("google-health") or {}
        provider = get_provider("google-health")
        auth = provider.auth_status(credentials)
        return {
            "version": __version__,
            "provider": settings.get("provider"),
            "available_providers": available_providers(),
            "goals": settings.get("goals"),
            "sync_interval_minutes": settings.get("sync_interval_minutes"),
            "default_redirect_uri": default_redirect_uri,
            "google_health": {
                "configured": auth["configured"],
                "connected": auth["connected"],
                "needs_reconnect": auth["needs_reconnect"],
                "client_id": auth.get("client_id") or "",
                "has_client_secret": bool(credentials.get("client_secret")),
                "redirect_uri": credentials.get("redirect_uri") or default_redirect_uri,
            },
            "ai": ai_service.public_settings(conn),
        }

    @app.get("/api/v1/settings")
    def get_settings() -> dict:
        return settings_payload()

    @app.post("/api/v1/settings")
    def update_settings(payload: dict | None = None) -> dict:
        patch = payload or {}
        allowed: dict = {}
        if "provider" in patch:
            provider_name = str(patch.get("provider") or "")
            if provider_name not in available_providers():
                raise HTTPException(status_code=400, detail=t('server.api.app.update_settings.e1c5e7d36', provider_name=provider_name))
            allowed["provider"] = provider_name
        if "goals" in patch:
            goals_raw = patch.get("goals") or {}
            goals: dict = {}
            for key, label in (("steps", t('server.api.app.update_settings.065cbd7d1')), ("sleep_minutes", t('server.api.app.update_settings.33d67ae27'))):
                if key in goals_raw:
                    value = goals_raw[key]
                    if value is None:
                        continue
                    if not isinstance(value, (int, float)) or value <= 0:
                        raise HTTPException(status_code=400, detail=t('server.api.app.update_settings.595b16d4c', label=label))
                    goals[key] = int(value)
            allowed["goals"] = {**storage.DEFAULT_SETTINGS["goals"], **goals}
        if "sync_interval_minutes" in patch:
            try:
                interval = int(patch.get("sync_interval_minutes") or 0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=t('server.api.app.update_settings.a28ac8333')) from exc
            if interval < 0:
                raise HTTPException(status_code=400, detail=t('server.api.app.update_settings.469c45b87'))
            allowed["sync_interval_minutes"] = interval
        storage.save_settings(conn, allowed)
        return settings_payload()

    @app.post("/api/v1/settings/credentials")
    def save_credentials(payload: dict | None = None) -> dict:
        body = payload or {}
        existing = engine.credentials_for("google-health") or {}
        client_id = str(body.get("client_id") or "").strip()
        client_secret = str(body.get("client_secret") or "").strip()
        redirect_uri = str(body.get("redirect_uri") or existing.get("redirect_uri") or default_redirect_uri).strip()
        if not client_id:
            raise HTTPException(status_code=400, detail=t('server.api.app.save_credentials.a2d99d922'))
        if not client_secret:
            client_secret = str(existing.get("client_secret") or "")
        if not client_secret:
            raise HTTPException(status_code=400, detail=t('server.api.app.save_credentials.ef53b31e2'))
        if not (redirect_uri.startswith("http://") or redirect_uri.startswith("https://")):
            raise HTTPException(status_code=400, detail=t('server.api.app.save_credentials.d395d07f7'))
        credentials = {**existing, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}
        engine.save_credentials_for("google-health", credentials)
        return settings_payload()

    @app.post("/api/v1/disconnect")
    def disconnect() -> dict:
        provider = get_provider("google-health")
        credentials = engine.credentials_for("google-health")
        if credentials:
            provider.revoke(credentials=credentials, http=engine.http())
        storage.delete_credentials(conn, "google-health")
        return {"ok": True, **settings_payload()}

    # ---------- OAuth ----------

    @app.get("/oauth/start")
    def oauth_start() -> RedirectResponse:
        credentials = engine.credentials_for("google-health") or {}
        if not credentials.get("client_id") or not credentials.get("client_secret"):
            raise HTTPException(status_code=400, detail=t('server.api.app.oauth_start.1e09bbdc7'))
        provider = get_provider("google-health")
        verifier, challenge = provider.new_pkce()
        state = secrets.token_urlsafe(24)
        storage.save_oauth_state(conn, state, verifier)
        url = provider.build_auth_url(
            credentials=credentials,
            redirect_uri=credentials.get("redirect_uri") or default_redirect_uri,
            state=state,
            code_challenge=challenge,
        )
        return RedirectResponse(url, status_code=302)

    @app.get("/oauth/callback")
    def oauth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
        if error:
            return RedirectResponse(f"/settings?oauth=error&message={quote(error)}", status_code=302)
        if not code or not state:
            raise HTTPException(status_code=400, detail=t('server.api.app.oauth_callback.48e772f42'))
        record = storage.pop_oauth_state(conn, state)
        if record is None:
            raise HTTPException(status_code=400, detail=t('server.api.app.oauth_callback.76857eacb'))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["created_at"])).total_seconds()
        if age > OAUTH_STATE_TTL_SECONDS:
            raise HTTPException(status_code=400, detail=t('server.api.app.oauth_callback.b390ee888'))
        credentials = engine.credentials_for("google-health") or {}
        if not credentials.get("client_id"):
            raise HTTPException(status_code=400, detail=t('server.api.app.oauth_callback.c61bb2ab8'))
        provider = get_provider("google-health")
        try:
            tokens = provider.exchange_code(
                credentials=credentials,
                redirect_uri=credentials.get("redirect_uri") or default_redirect_uri,
                code=code,
                code_verifier=record["verifier"],
                http=engine.http(),
            )
        except ProviderError as exc:
            return RedirectResponse(f"/settings?oauth=error&message={quote(str(exc))}", status_code=302)
        credentials["tokens"] = tokens
        credentials["needs_reconnect"] = False
        engine.save_credentials_for("google-health", credentials)
        engine.start_background([shift_iso(iso_today(), -1), iso_today()])
        return RedirectResponse("/settings?oauth=ok", status_code=302)

    # ---------- 数据管理 ----------

    @app.get("/api/v1/export")
    def export() -> JSONResponse:
        payload = storage.export_all(conn)
        filename = f"health-by-export-{iso_today()}.json"
        return JSONResponse(payload, headers={"content-disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/v1/data/clear")
    def clear_data(payload: dict | None = None) -> dict:
        body = payload or {}
        if body.get("confirm") != CONFIRM_TOKEN:
            raise HTTPException(status_code=400, detail=t('server.api.app.clear_data.7299410f8'))
        storage.delete_all_data(conn)
        return {"ok": True}

    # ---------- 静态页面 ----------

    web_dir = cfg.web_dir
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/settings")
    def settings_page() -> FileResponse:
        return FileResponse(web_dir / "settings.html")

    return app
