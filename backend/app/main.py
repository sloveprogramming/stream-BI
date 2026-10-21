from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

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

    # 数据质量巡检：定时跑规则，异常落库 + 记审计（与同步/报表/监控同一套调度器）
    from app.quality_scheduler import refresh_quality_jobs

    refresh_quality_jobs(scheduler, session_factory)

    # 授权到期自动过期：每小时把已过 expires_at 的授权置 expired。
    # 安全边界其实在读时的 active_grants_query，这个任务只是把事实写进状态，
    # 让管理界面显示"已过期"、审计能追溯"什么时候过期的"。
    from app.grants_expiry import register_job as register_expiry_job

    register_expiry_job(scheduler, session_factory)

    # 主动监控：定时跑指标，命中异常才启动 Agent 归因并生成简报
    if settings.monitor_enabled:
        from app.monitor.scheduler import refresh_monitor_jobs

        refresh_monitor_jobs(scheduler, session_factory)

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

    # ERP 侧维护：① 结构演进（给已有任务补新字段）② 发现新表并建任务
    # 数据落到 ods 后，再由上面的 _discovery_job 上架为数据集
    if settings.erp_autosync_enabled:
        from datetime import datetime, timedelta, timezone

        from apscheduler.triggers.date import DateTrigger

        from app.sync.evolution import evolve_sync_schemas

        def _run_soon(task_id: int) -> None:
            """用一次性任务在调度器线程里立刻拉一次，避免阻塞启动/扫描线程。"""
            scheduler.add_job(
                execute_task,
                DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=2)),
                args=[task_id, "auto"],
                id=f"autosync_first_{task_id}",
                replace_existing=True,
            )

        def _erp_autosync_job() -> None:
            touched: list[int] = []
            try:
                evolved = evolve_sync_schemas(
                    erp_engine, pg_engine, session_factory, settings.discovery_schema
                )
                touched += [item["task_id"] for item in evolved["evolved"]]
            except Exception:  # noqa: BLE001 —— ERP 不可达等，不影响其他任务
                logger.exception("ERP 结构演进失败")
            try:
                result = discover_sync_tasks(erp_engine, session_factory, settings)
            except Exception:  # noqa: BLE001
                logger.exception("ERP 自动同步扫描失败")
                result = {"created": []}
            if result["created"]:
                refresh_jobs(scheduler, session_factory)  # 让新任务进入 cron 调度
                touched += [item["id"] for item in result["created"]]
            if not touched or not settings.erp_autosync_run_now:
                return
            for task_id in dict.fromkeys(touched):  # 去重且保序
                _run_soon(task_id)

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

    from app.assistant.agent_api import router as agent_router

    app.include_router(agent_router, prefix="/api/v1")

    from app.api.flow_api import router as flow_router

    app.include_router(flow_router, prefix="/api/v1")

    from app.api.provider_api import router as provider_router

    app.include_router(provider_router, prefix="/api/v1")

    from app.api.lineage_api import router as lineage_router

    app.include_router(lineage_router, prefix="/api/v1")

    # 治理补全：租户 / 权限申请与审批 / 数据质量巡检 / 指标血缘
    from app.api.grant_api import router as grant_router
    from app.api.metric_api import router as metric_router
    from app.api.quality_api import router as quality_router
    from app.api.tenant_api import router as tenant_router
    from app.api.dashboard_api import router as dashboard_router

    # 看板查询抛的 PermissionError 统一转 403（否则 FastAPI 报 500）
    @app.exception_handler(PermissionError)
    async def _permission_denied(_request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    app.include_router(tenant_router, prefix="/api/v1")
    app.include_router(grant_router, prefix="/api/v1")
    app.include_router(quality_router, prefix="/api/v1")
    app.include_router(metric_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")

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









