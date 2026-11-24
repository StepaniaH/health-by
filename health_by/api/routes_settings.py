"""通用设置、OAuth 客户端凭据配置与断开连接路由。"""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Request

from health_by import storage
from health_by.i18n import t
from health_by.providers import available_providers, get_provider

from .common import build_settings_payload
from .security import LocalizedRoute

router = APIRouter(route_class=LocalizedRoute)


@router.get("/api/v1/settings")
def get_settings(request: Request) -> dict:
    return build_settings_payload(request)


@router.post("/api/v1/settings")
def update_settings(request: Request, payload: dict | None = None) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    patch = payload or {}
    allowed: dict = {}
    if "provider" in patch:
        provider_name = str(patch.get("provider") or "")
        if provider_name not in available_providers():
            raise HTTPException(
                status_code=400,
                detail=t("server.api.app.update_settings.e1c5e7d36", provider_name=provider_name),
            )
        allowed["provider"] = provider_name
    goals_patch: dict | None = None
    if "goals" in patch:
        goals_raw = patch.get("goals")
        if goals_raw is not None and not isinstance(goals_raw, dict):
            raise HTTPException(
                status_code=400,
                detail=t("server.api.app.update_settings.595b16d4c", label=t("server.api.app.update_settings.065cbd7d1")),
            )
        goals_raw = goals_raw or {}
        goals: dict = {}
        for key, label in (
            ("steps", t("server.api.app.update_settings.065cbd7d1")),
            ("sleep_minutes", t("server.api.app.update_settings.33d67ae27")),
        ):
            if key in goals_raw:
                value = goals_raw[key]
                if value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or (isinstance(value, float) and not value.is_integer())
                    or int(value) <= 0
                ):
                    raise HTTPException(status_code=400, detail=t("server.api.app.update_settings.595b16d4c", label=label))
                goals[key] = int(value)
        goals_patch = goals
    if "sync_interval_minutes" in patch:
        try:
            raw_interval = patch.get("sync_interval_minutes")
            if isinstance(raw_interval, bool):
                raise TypeError
            if isinstance(raw_interval, float) and (not math.isfinite(raw_interval) or not raw_interval.is_integer()):
                raise ValueError
            interval = int(raw_interval or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=t("server.api.app.update_settings.a28ac8333")) from exc
        if interval < 0:
            raise HTTPException(status_code=400, detail=t("server.api.app.update_settings.469c45b87"))
        allowed["sync_interval_minutes"] = interval
    with conn_lock:
        if goals_patch is not None:
            existing_goals = storage.load_settings(conn).get("goals") or {}
            allowed["goals"] = {**storage.DEFAULT_SETTINGS["goals"], **existing_goals, **goals_patch}
        storage.save_settings(conn, allowed)
    return build_settings_payload(request)


@router.post("/api/v1/settings/credentials")
def save_credentials(request: Request, payload: dict | None = None) -> dict:
    body = payload or {}
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    default_redirect_uri = request.app.state.default_redirect_uri
    with conn_lock:
        existing = engine.credentials_for("google-health") or {}
        client_id = str(body.get("client_id") or "").strip()
        client_secret = str(body.get("client_secret") or "").strip()
        redirect_uri = str(body.get("redirect_uri") or existing.get("redirect_uri") or default_redirect_uri).strip()
        if not client_id:
            raise HTTPException(status_code=400, detail=t("server.api.app.save_credentials.a2d99d922"))
        if not client_secret:
            client_secret = str(existing.get("client_secret") or "")
        if not client_secret:
            raise HTTPException(status_code=400, detail=t("server.api.app.save_credentials.ef53b31e2"))
        if not (redirect_uri.startswith("http://") or redirect_uri.startswith("https://")):
            raise HTTPException(status_code=400, detail=t("server.api.app.save_credentials.d395d07f7"))
        credentials = {
            **existing,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        engine.save_credentials_for("google-health", credentials)
    return build_settings_payload(request)


@router.post("/api/v1/disconnect")
def disconnect(request: Request) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    with conn_lock:
        provider = get_provider("google-health")
        credentials = engine.credentials_for("google-health")
    if credentials:
        provider.revoke(credentials=credentials, http=engine.http())
    with conn_lock:
        storage.delete_credentials(conn, "google-health")
    return {"ok": True, **build_settings_payload(request)}
