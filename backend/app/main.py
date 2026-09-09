from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # Task 7 在此接入调度器


def create_app() -> FastAPI:
    app = FastAPI(title="BIsql 数据分发中台", lifespan=lifespan)
    app.include_router(health_router, prefix="/api/v1")
    return app
