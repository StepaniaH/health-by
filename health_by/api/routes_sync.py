"""数据同步触发与进度/历史查询路由。"""

from __future__ import annotations

from datetime import date as date_type
import math

from fastapi import APIRouter, HTTPException, Request

from health_by import storage
from health_by.i18n import t
from health_by.util import iso_range, iso_today

from .common import SYNC_MAX_DAYS, _dates_between, _validate_date
from .security import LocalizedRoute

router = APIRouter(route_class=LocalizedRoute)


@router.post("/api/v1/sync")
def sync(request: Request, payload: dict | None = None) -> dict:
    engine = request.app.state.engine
    body = payload or {}
    dates: list[str] = []
    if body.get("dates"):
        raw_dates = body.get("dates")
        if not isinstance(raw_dates, list):
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.60bba9b6e"))
        dates = [_validate_date(str(item)) for item in raw_dates]
    elif body.get("start") or body.get("end"):
        start = _validate_date(str(body.get("start") or body.get("end")))
        end = _validate_date(str(body.get("end") or body.get("start")))
        if start > end:
            start, end = end, start
        if (date_type.fromisoformat(end) - date_type.fromisoformat(start)).days + 1 > SYNC_MAX_DAYS:
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.00b5cfc7d", SYNC_MAX_DAYS=SYNC_MAX_DAYS))
        dates = _dates_between(start, end)
    elif body.get("date"):
        dates = [_validate_date(str(body["date"]))]
    elif "days" in body and body.get("days") not in (None, ""):
        raw_days = body.get("days")
        if isinstance(raw_days, bool) or (
            isinstance(raw_days, float) and (not math.isfinite(raw_days) or not raw_days.is_integer())
        ):
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.invalid_days"))
        try:
            days = int(raw_days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.invalid_days")) from exc
        if days < 0:
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.invalid_days"))
        if days == 0:
            dates = [iso_today()]
        elif days > SYNC_MAX_DAYS:
            raise HTTPException(status_code=400, detail=t("server.api.app.sync.00b5cfc7d", SYNC_MAX_DAYS=SYNC_MAX_DAYS))
        else:
            dates = iso_range(iso_today(), days)
    else:
        dates = [iso_today()]
    if len(dates) > SYNC_MAX_DAYS:
        raise HTTPException(status_code=400, detail=t("server.api.app.sync.00b5cfc7d", SYNC_MAX_DAYS=SYNC_MAX_DAYS))
    with request.app.state.conn_lock:
        job = engine.start_background(dates)
    return {
        "started": True,
        "dates": dates,
        "outstanding": job["outstanding"],
        "added": job["added"],
        "workers": job["workers"],
    }


@router.get("/api/v1/sync/status")
def sync_status(request: Request) -> dict:
    return request.app.state.engine.status()


@router.get("/api/v1/sync/runs")
def sync_runs(request: Request, limit: int = 10) -> dict:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    with conn_lock:
        return {"runs": storage.list_sync_runs(conn, limit=max(1, min(50, limit)))}
