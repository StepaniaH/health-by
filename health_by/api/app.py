"""FastAPI 应用工厂：组装路由、安全中间件与生命周期管理。

约定：所有处理器都是同步函数（FastAPI 线程池执行），共用同一 SQLite 连接
（连接已 check_same_thread=False，写入由 Engine 单飞锁与 SQLite 事务保护）。
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..ai.service import AIService
from ..config import Config
from ..coverage import index_raw_dates, raw_coverage
from ..db import connect, migrate
from ..engine import Engine
from . import routes_ai, routes_data, routes_oauth, routes_settings, routes_sync
from .common import CONFIRM_TOKEN, OAUTH_STATE_TTL_SECONDS, SUMMARY_TREND_DAYS, SYNC_MAX_DAYS
from .security import LocalizedRoute, register_security_middleware

__all__ = [
    "CONFIRM_TOKEN",
    "LocalizedRoute",
    "OAUTH_STATE_TTL_SECONDS",
    "SUMMARY_TREND_DAYS",
    "SYNC_MAX_DAYS",
    "create_app",
    "raw_coverage",
]


def create_app(cfg: Config) -> FastAPI:
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    migrate(conn)
    engine = Engine(cfg, conn)
    ai_service = AIService(cfg)
    default_redirect_uri = f"http://127.0.0.1:{cfg.port}/oauth/callback"

    coverage_cache = None
    coverage_signature = None
    conn_lock = threading.RLock()

    def coverage_snapshot() -> dict:
        # 原始留档可能很大：同一应用内只在数据库变化后重扫。
        # total_changes 覆盖本连接写入；data_version 覆盖同步引擎等其它连接的提交。
        nonlocal coverage_cache, coverage_signature
        with conn_lock:
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
        with conn_lock:
            index_raw_dates(conn)
        stop_event = threading.Event()
        auto_sync_thread = threading.Thread(
            target=engine.auto_sync_loop, args=(stop_event,), daemon=True
        )
        auto_sync_thread.start()
        yield
        stop_event.set()
        auto_sync_thread.join(timeout=5.0)
        engine.shutdown()
        engine.close()
        with conn_lock:
            conn.close()

    app = FastAPI(
        title="Health By",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.router.route_class = LocalizedRoute
    register_security_middleware(app, cfg)

    app.state.cfg = cfg
    app.state.conn = conn
    app.state.conn_lock = conn_lock
    app.state.engine = engine
    app.state.ai = ai_service
    app.state.default_redirect_uri = default_redirect_uri
    app.state.coverage_snapshot = coverage_snapshot

    for router_module in (routes_data, routes_ai, routes_sync, routes_settings, routes_oauth):
        app.include_router(router_module.router)

    web_dir = cfg.web_dir
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/settings")
    def settings_page() -> FileResponse:
        return FileResponse(web_dir / "settings.html")

    return app
