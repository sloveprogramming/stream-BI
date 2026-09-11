from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from app.db import make_engine, make_session_factory
    from app.scheduler import execute_task, refresh_jobs
    from app.settings import get_settings
    from app.sync.discovery import discover_datasets
    from app.sync.erp_autosync import discover_sync_tasks
    from app.sync.puller import make_erp_engine

    logger = logging.getLogger("app.main")
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

    # 定时扫描 ods 新表并自动注册为数据集（ERP 定时同步建表后前端几秒内可见）
    if settings.discovery_enabled:
        def _discovery_job() -> None:
            try:
                discover_datasets(
                    session_factory, pg_engine, settings.discovery_schema
                )
            except Exception:  # noqa: BLE001
                logger.exception("数据集自动发现失败")

        try:
            _discovery_job()
        except Exception:  # noqa: BLE001
            logger.exception("启动时数据集自动发现失败")
        scheduler.add_job(
            _discovery_job,
            IntervalTrigger(seconds=settings.discovery_interval_seconds),
            id="dataset_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    # ERP 侧自动同步：扫描 ERP 新表 → 自动建同步任务 → 装载 cron → 需要时立即拉一次
    # 数据落到 ods 后，再由上面的 _discovery_job 上架为数据集
    if settings.erp_autosync_enabled:
        from datetime import datetime, timedelta, timezone

        from apscheduler.triggers.date import DateTrigger

        def _erp_autosync_job() -> None:
            try:
                result = discover_sync_tasks(erp_engine, session_factory, settings)
            except Exception:  # noqa: BLE001 —— ERP 不可达等，不影响其他任务
                logger.exception("ERP 自动同步扫描失败")
                return
            if not result["created"]:
                return
            refresh_jobs(scheduler, session_factory)  # 让新任务进入 cron 调度
            if not settings.erp_autosync_run_now:
                return
            # 用一次性任务在调度器线程里立刻拉一次，避免阻塞启动/扫描线程
            for item in result["created"]:
                scheduler.add_job(
                    execute_task,
                    DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=2)),
                    args=[item["id"], "auto"],
                    id=f"autosync_first_{item['id']}",
                    replace_existing=True,
                )

        try:
            _erp_autosync_job()
        except Exception:  # noqa: BLE001
            logger.exception("启动时 ERP 自动同步扫描失败")
        scheduler.add_job(
            _erp_autosync_job,
            IntervalTrigger(seconds=settings.erp_autosync_interval_seconds),
            id="erp_autosync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

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

    from app.assistant.chat_api import router as assistant_router

    app.include_router(assistant_router, prefix="/api/v1")

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









