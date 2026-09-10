from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.db import make_engine, make_session_factory
    from app.scheduler import refresh_jobs
    from app.settings import get_settings
    from app.sync.puller import make_erp_engine

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    erp_engine = make_erp_engine(
        settings.erp_database_url, settings.erp_query_timeout_seconds
    )
    session_factory = make_session_factory(pg_engine)
    app.state.settings = settings
    app.state.pg_engine = pg_engine
    app.state.erp_engine = erp_engine
    app.state.session_factory = session_factory

    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    from app.reports.scheduler import refresh_report_jobs

    refresh_jobs(scheduler, session_factory)
    refresh_report_jobs(scheduler, session_factory)
    scheduler.start()
    app.state.scheduler = scheduler

    from contextlib import asynccontextmanager as _acm  # noqa
    from app.mcp_server import mcp_app as _mcp

    async with _mcp.session_manager.run():
        yield
    scheduler.shutdown(wait=False)
    erp_engine.dispose()
    pg_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="BIsql 数据分发中台", lifespan=lifespan)
    from app.api.auth_api import router as auth_router

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")

    from app.api.sync_api import router as sync_router

    app.include_router(sync_router, prefix="/api/v1")

    from app.api.admin_api import router as admin_router

    app.include_router(admin_router, prefix="/api/v1")

    from app.api.data_api import router as data_router

    app.include_router(data_router, prefix="/api/v1")

    from app.api.export_api import router as export_router

    app.include_router(export_router, prefix="/api/v1")

    from app.api.reports_api import router as reports_router

    app.include_router(reports_router, prefix="/api/v1")

    from app.api.web_support import router as web_support_router

    app.include_router(web_support_router, prefix="/api/v1")

    # 前端静态托管（存在时启用；SPA fallback 返回 index.html）
    from pathlib import Path

    frontend_dist = None
    for _parent in Path(__file__).resolve().parents:
        if (_parent / "frontend" / "dist").is_dir():
            frontend_dist = _parent / "frontend" / "dist"
            break
    if frontend_dist:
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            file = frontend_dist / full_path
            if full_path and file.is_file():
                return FileResponse(file)
            return FileResponse(frontend_dist / "index.html")

    # MCP streamable-HTTP（挂载在 /，内部路由 /mcp；无状态 POST 请求绕过 GET-only catch-all）
    from app.mcp_auth import MCPAuthMiddleware
    from app.mcp_server import mcp_app

    app.add_middleware(MCPAuthMiddleware)
    app.mount("/", mcp_app.streamable_http_app())
    return app


# uvicorn 入口：app.main:app
app = create_app()









