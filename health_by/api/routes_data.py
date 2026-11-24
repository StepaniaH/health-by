"""状态、覆盖度、日报摘要、趋势洞察、流式导出与数据清理路由。"""

from __future__ import annotations

from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from health_by import __version__, analysis, metrics, reliability, storage
from health_by.db import connect
from health_by.i18n import t
from health_by.insights import build_insights
from health_by.util import iso_today, shift_iso, utc_now_iso

from .common import CONFIRM_TOKEN, SUMMARY_TREND_DAYS, _derive_activity, _validate_date
from .security import LocalizedRoute

router = APIRouter(route_class=LocalizedRoute)


@router.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "time": utc_now_iso()}


@router.get("/api/v1/status")
def status(request: Request) -> dict:
    cfg = request.app.state.cfg
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    with conn_lock:
        settings = storage.load_settings(conn)
        last_run = next(iter(storage.list_sync_runs(conn, limit=1)), None)
        provider_status = engine.auth_status()
        days_stored = storage.count_days(conn)
        latest_date = storage.latest_day_date(conn)
    return {
        "version": __version__,
        "provider": provider_status,
        "goals": settings.get("goals"),
        "sync_interval_minutes": settings.get("sync_interval_minutes"),
        "data_dir": str(cfg.data_dir),
        "days_stored": days_stored,
        "latest_date": latest_date,
        "today": iso_today(),
        "last_sync_run": last_run,
        "sync": engine.status(),
    }


@router.get("/api/v1/coverage")
def coverage(request: Request) -> dict:
    return request.app.state.coverage_snapshot()


@router.get("/api/v1/summary")
def summary(request: Request, date: str | None = None) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    coverage_snapshot = request.app.state.coverage_snapshot
    analysis_days = max(SUMMARY_TREND_DAYS, metrics.BASELINE_WINDOW_DAYS + 8)
    with conn_lock:
        selected = _validate_date(date) if date else (storage.latest_day_date(conn) or iso_today())
        day = storage.get_day(conn, selected)
        if day is None:
            raise HTTPException(status_code=404, detail=t("server.api.app.summary.88f07f765", selected=selected))
        cov = coverage_snapshot()
        settings = storage.load_settings(conn)
        analysis_trends = storage.get_trends(conn, selected, analysis_days)
        history = storage.get_day_window(conn, selected, reliability.WINDOW_DAYS)
    day = _derive_activity(day)
    day = {**day, "coverage": {**(day.get("coverage") or {}), **cov}}
    trend_start = shift_iso(selected, -(SUMMARY_TREND_DAYS - 1))
    trends = [point for point in analysis_trends if point["date"] >= trend_start]
    readings = [
        (point["date"], {key: metrics.to_float(value) for key, value in analysis.comparison_values(point).items()})
        for point in history
    ]
    current = {key: metrics.to_float(value) for key, value in analysis.comparison_values(day).items()}
    assessment = reliability.assess(readings, selected, current)
    result_analysis = analysis.build_analysis(
        day,
        analysis_trends,
        settings.get("goals") or {},
        display_values=assessment["display_values"],
        display_modes=assessment["display_mode"],
    )
    result_analysis.update(assessment)
    return {
        "date": selected,
        "day": day,
        "trends": trends,
        "goals": settings.get("goals") or {},
        "analysis": result_analysis,
    }


@router.get("/api/v1/dates")
def dates(request: Request) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    with conn_lock:
        return {"dates": storage.available_dates(conn), "today": iso_today()}


@router.get("/api/v1/insights")
def insights(request: Request, date: str | None = None, days: int = 30, include_hidden: int = 0) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    window = max(7, min(90, days))
    with conn_lock:
        selected = _validate_date(date) if date else (storage.latest_day_date(conn) or iso_today())
        history = storage.get_day_window(conn, selected, window + metrics.BASELINE_WINDOW_DAYS + 7)
        settings = storage.load_settings(conn)
    window_start = shift_iso(selected, -(window - 1))
    window_days = [item for item in history if str(item.get("date") or "") >= window_start]
    return build_insights(
        window_days,
        settings.get("goals") or {},
        end_date=selected,
        window_days=window,
        include_hidden=bool(include_hidden),
        history_days=history,
    )


@router.get("/api/v1/export")
def export(request: Request) -> StreamingResponse:
    cfg = request.app.state.cfg
    filename = f"health-by-export-{iso_today()}.json"

    def stream():
        with closing(connect(cfg.db_path)) as export_conn:
            export_conn.execute("BEGIN")
            try:
                yield from storage.iter_export_json(export_conn)
            finally:
                if export_conn.in_transaction:
                    export_conn.rollback()

    return StreamingResponse(
        stream(),
        media_type="application/json",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/v1/data/clear")
def clear_data(request: Request, payload: dict | None = None) -> dict:
    body = payload or {}
    if body.get("confirm") != CONFIRM_TOKEN:
        raise HTTPException(status_code=400, detail=t("server.api.app.clear_data.7299410f8"))
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = getattr(request.app.state, "engine", None)
    with conn_lock:
        if engine is not None:
            engine.clear_all(conn)
        else:
            storage.delete_all_data(conn)
    return {"ok": True}
