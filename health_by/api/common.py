"""API 公共常量、参数校验、活动字段派生与设置视图构造。"""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta

from fastapi import HTTPException, Request

from health_by import __version__, insights as insights_module, metrics, storage
from health_by.i18n import t
from health_by.providers import available_providers, get_provider

# 趋势窗口的唯一权威定义在 insights 层（它需要回显给前端），这里只引用。
SUMMARY_TREND_DAYS = insights_module.SUMMARY_TREND_DAYS
OAUTH_STATE_TTL_SECONDS = 15 * 60
SYNC_MAX_DAYS = 90
CONFIRM_TOKEN = "DELETE ALL DATA"


def _validate_date(value: str) -> str:
    try:
        return date_type.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=t("server.api.app._validate_date.5cd941f20", value=value)) from exc


def _dates_between(start: str, end: str) -> list[str]:
    first = date_type.fromisoformat(start)
    last = date_type.fromisoformat(end)
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def _derive_activity(day: dict) -> dict:
    """把派生字段补进日快照（读时算，不写回存档）。"""
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


def build_settings_payload(request: Request) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    ai_service = request.app.state.ai
    default_redirect_uri = request.app.state.default_redirect_uri
    with conn_lock:
        settings = storage.load_settings(conn)
        credentials = engine.credentials_for("google-health") or {}
        provider = get_provider("google-health")
        auth = provider.auth_status(credentials)
        ai_public = ai_service.public_settings(conn)
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
        "ai": ai_public,
    }
