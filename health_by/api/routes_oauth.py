"""Google Health OAuth PKCE 授权发起与回调路由。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from health_by import storage
from health_by.i18n import t
from health_by.providers import get_provider
from health_by.providers.base import ProviderError
from health_by.util import iso_today, shift_iso

from .common import OAUTH_STATE_TTL_SECONDS
from .security import LocalizedRoute

router = APIRouter(route_class=LocalizedRoute)


@router.get("/oauth/start")
def oauth_start(request: Request) -> RedirectResponse:
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    default_redirect_uri = request.app.state.default_redirect_uri
    with conn_lock:
        credentials = engine.credentials_for("google-health") or {}
        if not credentials.get("client_id") or not credentials.get("client_secret"):
            raise HTTPException(status_code=400, detail=t("server.api.app.oauth_start.1e09bbdc7"))
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


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        return RedirectResponse(f"/settings?oauth=error&message={quote(error)}", status_code=302)
    if not code or not state:
        raise HTTPException(status_code=400, detail=t("server.api.app.oauth_callback.48e772f42"))
    conn = request.app.state.conn
    conn_lock = request.app.state.conn_lock
    engine = request.app.state.engine
    default_redirect_uri = request.app.state.default_redirect_uri
    with conn_lock:
        record = storage.pop_oauth_state(conn, state)
        if record is None:
            raise HTTPException(status_code=400, detail=t("server.api.app.oauth_callback.76857eacb"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["created_at"])).total_seconds()
        if age > OAUTH_STATE_TTL_SECONDS:
            raise HTTPException(status_code=400, detail=t("server.api.app.oauth_callback.b390ee888"))
        credentials = engine.credentials_for("google-health") or {}
        if not credentials.get("client_id"):
            raise HTTPException(status_code=400, detail=t("server.api.app.oauth_callback.c61bb2ab8"))
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
    with conn_lock:
        credentials["tokens"] = tokens
        credentials["needs_reconnect"] = False
        engine.save_credentials_for("google-health", credentials)
    engine.start_background([shift_iso(iso_today(), -1), iso_today()])
    return RedirectResponse("/settings?oauth=ok", status_code=302)
