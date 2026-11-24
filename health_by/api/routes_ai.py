"""BYOK AI 解读、连接测试、模型列举与配置路由（仅显式请求时触发外发）。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from health_by.ai.client import AIError
from health_by.i18n import t

from .security import LocalizedRoute

router = APIRouter(route_class=LocalizedRoute)


@router.post("/api/v1/ai/models")
def ai_models(request: Request) -> dict:
    return request.app.state.ai.list_models()


@router.post("/api/v1/ai/test")
def ai_test(request: Request) -> dict:
    return request.app.state.ai.test_connection()


@router.get("/api/v1/ai/analysis")
def ai_analysis(
    request: Request,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    return request.app.state.ai.get(date, start=start, end=end)


@router.post("/api/v1/ai/analyze")
def ai_analyze(request: Request, payload: dict) -> dict:
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise AIError(t("server.api.app.ai_analyze.6268f8803"))
    return request.app.state.ai.analyze(
        payload.get("date"),
        start=payload.get("start"),
        end=payload.get("end"),
        force=force,
    )


@router.post("/api/v1/settings/ai")
def ai_settings(request: Request, payload: dict) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    ai_service = request.app.state.ai
    with conn_lock:
        return ai_service.configure(conn, payload)
