"""API 安全中间件与本地语言路由包装（Host 校验、同源 CSRF 校验、安全响应头）。"""

from __future__ import annotations

import ipaddress
from functools import wraps
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from health_by.ai.client import AIError
from health_by.config import Config, env_value
from health_by.i18n import language, localize, using_language

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class LocalizedRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        endpoint = kwargs.get("endpoint")
        if endpoint is not None:
            @wraps(endpoint)
            def translated(*args, **kwargs):
                return localize(endpoint(*args, **kwargs))

            kwargs["endpoint"] = translated
        super().__init__(*args, **kwargs)


def _parse_host_header(raw: str) -> tuple[str, int | None] | None:
    value = raw.strip()
    if not value or any(ch.isspace() for ch in value) or "/" in value or "@" in value or "\\" in value:
        return None
    try:
        parts = urlsplit(f"http://{value}")
    except ValueError:
        return None
    if not parts.hostname or parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port == 0:
        return None
    return parts.hostname.lower().rstrip("."), port


def _is_direct_local_ip(hostname: str) -> bool:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    if ip.is_unspecified or ip.is_multicast:
        return False
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK


def _allowed_hosts(cfg: Config) -> set[str]:
    hosts = {"localhost", "127.0.0.1", "::1"}
    configured = (cfg.host or "").strip("[]").lower().rstrip(".")
    if configured and configured not in {"0.0.0.0", "::"}:
        hosts.add(configured)
    raw_extra = env_value("ALLOWED_HOSTS", "") or ""
    for item in str(raw_extra).split(","):
        cleaned = item.strip().strip("[]").lower().rstrip(".")
        if cleaned:
            hosts.add(cleaned)
    return hosts


def _same_authority(url_value: str, request_host: tuple[str, int | None], request_scheme: str) -> bool:
    cleaned = url_value.strip()
    if not cleaned or cleaned.lower() == "null":
        return False
    try:
        parts = urlsplit(cleaned)
        origin_port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    origin_host = parts.hostname.lower().rstrip(".")
    if origin_host != request_host[0]:
        return False
    effective_origin_port = origin_port or (443 if parts.scheme == "https" else 80)
    if request_host[1] is not None:
        return effective_origin_port == request_host[1]
    req_port = 443 if request_scheme == "https" else 80
    return effective_origin_port == req_port or effective_origin_port in (80, 443)


def _apply_security_headers(response, path: str) -> None:
    response.headers.setdefault("Content-Security-Policy", _CSP_POLICY)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    else:
        response.headers.setdefault("Cache-Control", "no-store")


def register_security_middleware(app: FastAPI, cfg: Config) -> None:
    """注册语言切换、Host/CSRF 守卫与本地化异常处理器。"""

    @app.middleware("http")
    async def select_language(request: Request, call_next):
        selected = language(request.query_params.get("lang") or request.headers.get("accept-language", ""))
        with using_language(selected):
            response = await call_next(request)
        response.headers["Content-Language"] = selected
        response.headers["Vary"] = "Accept-Language"
        return response

    @app.middleware("http")
    async def security_guard(request: Request, call_next):
        path = request.url.path
        parsed_host = _parse_host_header(request.headers.get("host", ""))
        if parsed_host is None:
            response = JSONResponse({"detail": "Forbidden: invalid Host header."}, status_code=403)
            _apply_security_headers(response, path)
            return response
        hostname, _ = parsed_host
        allowed = _allowed_hosts(cfg)
        is_testclient = (
            hostname == "testserver"
            and request.client is not None
            and request.client.host == "testclient"
        )
        host_ok = (
            "*" in allowed
            or hostname in allowed
            or is_testclient
            or (cfg.host in ("0.0.0.0", "::") and _is_direct_local_ip(hostname))
        )
        if not host_ok:
            response = JSONResponse({"detail": "Forbidden: invalid Host header."}, status_code=403)
            _apply_security_headers(response, path)
            return response

        is_state_changing = request.method.upper() not in ("GET", "HEAD", "OPTIONS") or path == "/oauth/start"
        if is_state_changing:
            origin = request.headers.get("origin")
            sec_fetch_site = request.headers.get("sec-fetch-site")
            referer = request.headers.get("referer")
            if origin is not None and not _same_authority(origin, parsed_host, request.url.scheme):
                response = JSONResponse({"detail": "Forbidden: cross-origin request rejected."}, status_code=403)
                _apply_security_headers(response, path)
                return response
            if sec_fetch_site is not None and sec_fetch_site.strip().lower() not in ("same-origin", "none"):
                response = JSONResponse({"detail": "Forbidden: cross-site request rejected."}, status_code=403)
                _apply_security_headers(response, path)
                return response
            if origin is None and sec_fetch_site is None and referer is not None:
                if not _same_authority(referer, parsed_host, request.url.scheme):
                    response = JSONResponse({"detail": "Forbidden: cross-origin referer rejected."}, status_code=403)
                    _apply_security_headers(response, path)
                    return response

        response = await call_next(request)
        _apply_security_headers(response, path)
        return response

    @app.exception_handler(HTTPException)
    async def localized_http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": localize(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(AIError)
    async def ai_error_handler(request: Request, exc: AIError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)
