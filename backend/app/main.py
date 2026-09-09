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
    refresh_jobs(scheduler, session_factory)
    scheduler.start()
    app.state.scheduler = scheduler
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
    return app





