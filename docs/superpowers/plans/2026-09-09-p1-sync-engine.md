# P1 同步引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建中台后端骨架，实现 ERP(MySQL) → PostgreSQL ods 层的定时增量同步引擎（含全量比对回退、重试告警），并暴露同步监控 API。

**Architecture:** FastAPI 单体进程内嵌 APScheduler。同步引擎按数据集配置（元数据表 `sync_tasks`）以水位线增量拉取 ERP MySQL 数据，upsert 到 PostgreSQL 的 `ods` schema；无增量字段的表退化为全量哈希比对；失败按指数退避重试并推送 webhook 告警；每次运行写 `sync_history`。监控 API 提供任务列表、手动触发、历史查询。

**Tech Stack:** Python 3.13（本机，兼容 >=3.12）、FastAPI、SQLAlchemy 2.0、psycopg 3、PyMySQL、APScheduler 3.x、pydantic-settings、pytest + httpx、Docker（PostgreSQL 16 / MySQL 8）。

**Spec:** docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md（实现依据，执行者须同时阅读）

## Global Constraints

- 所有后端代码位于 `backend/` 目录，命令默认在 `backend/` 下执行。
- Python `requires-python = ">=3.12"`（本机 3.13.7）。
- ERP 只读约束：仅 SELECT；独立连接池；连接超时 10s；读取超时 30s（`BISQL_ERP_QUERY_TIMEOUT_SECONDS` 可调）。
- 水位线只在同步成功后推进，失败不推进、不丢数据。
- 增量拉取单批 5000 行（`BISQL_SYNC_BATCH_SIZE` 可调），流式游标分批。
- ods 表统一放 PostgreSQL `ods` schema，每表额外带 `_synced_at timestamptz` 列。
- 标识符（表名/列名）必须通过 `^[A-Za-z_][A-Za-z0-9_]*$` 校验后原样拼接（防注入）。
- 密钥与连接串只走 `.env`（`backend/.env`），`.env` 不入 git。
- API 路由前缀 `/api/v1`；P1 不做鉴权/权限（P2 加）。
- 所有表名/字段小写下划线命名；配置类字段用 SQLAlchemy JSON 类型。
- 注意：Codex 沙箱默认拒绝写 `.git`，git 提交步骤需在沙箱外（提权）执行。
- 每个任务完成即 commit（提权执行），提交信息用 conventional commits（feat/test/chore/docs）。

## File Structure（P1 结束后的后端布局）

```
backend/
├── pyproject.toml              # 依赖与 pytest 配置
├── .env.example                # 环境变量样例
├── alembic.ini
├── alembic/                    # 迁移脚本
├── app/
│   ├── __init__.py
│   ├── main.py                 # create_app + lifespan(调度器启停)
│   ├── settings.py             # pydantic-settings，get_settings()
│   ├── db.py                   # make_engine / make_session_factory
│   ├── models/
│   │   ├── __init__.py         # 导出 Base 与全部模型
│   │   ├── base.py             # DeclarativeBase
│   │   └── sync.py             # SyncTask / SyncHistory
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── spec.py             # SyncSpec 数据类 + spec_from_task
│   │   ├── puller.py           # ERP 拉取（增量/全量流式迭代）
│   │   ├── ods.py              # ods 建表（MySQL→PG 类型映射）
│   │   ├── writer.py           # upsert_rows
│   │   ├── engine.py           # run_sync 主流程 + 水位线 + 历史
│   │   ├── retry.py            # run_with_retry
│   │   └── alert.py            # send_alert (webhook)
│   ├── scheduler.py            # APScheduler 注册/刷新
│   └── api/
│       ├── __init__.py
│       ├── deps.py             # get_context 依赖
│       ├── health.py           # GET /api/v1/health
│       └── sync_api.py         # 同步监控 API
└── tests/
    ├── conftest.py             # pg/mysql fixture（docker，不可达则 skip）
    ├── unit/                   # sqlite/内存/打桩
    └── integration/            # 真连 docker pg/mysql
docker-compose.yml               # 仓库根：postgres:16 + mysql:8
ops/mysql-init/01-demo.sql       # ERP 演示表（E2E 用）
```

---

### Task 1: 项目骨架与健康检查

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/settings.py`, `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/api/health.py`, `backend/tests/unit/test_health.py`, `.gitignore`

**Interfaces:**
- Produces: `create_app() -> FastAPI`（后续任务在 lifespan 内接入调度器）；`get_settings() -> Settings`（lru_cache 单例）。

- [ ] **Step 1: 写失败测试** `backend/tests/unit/test_health.py`

```python
from fastapi.testclient import TestClient
from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: 运行确认失败** — Run: `python -m pytest tests/unit/test_health.py -v`；Expected: FAIL（ModuleNotFoundError: app）

- [ ] **Step 3: 写骨架**

`backend/pyproject.toml`：

```toml
[project]
name = "bisql"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
    "pymysql>=1.1",
    "cryptography>=42",
    "apscheduler>=3.10,<4",
    "pydantic-settings>=2.3",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "alembic>=1.13"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

`backend/app/settings.py`：

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BISQL_", extra="ignore")

    database_url: str = "postgresql+psycopg://bisql:bisql@localhost:55432/bisql"
    erp_database_url: str = "mysql+pymysql://root:root@localhost:53306/erp?charset=utf8mb4"
    erp_query_timeout_seconds: int = 30
    sync_batch_size: int = 5000
    sync_retry_attempts: int = 3
    sync_retry_base_delay: float = 5.0
    alert_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/app/api/health.py`：

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}
```

`backend/app/main.py`：

```python
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
```

`backend/app/__init__.py`、`backend/app/api/__init__.py` 为空文件。
仓库根 `.gitignore` 追加：`.env`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`backend/.venv/`。

- [ ] **Step 4: 安装依赖并跑通测试** — Run: `python -m pip install -e ".[dev]"` 然后 `python -m pytest tests/unit/test_health.py -v`；Expected: PASS

- [ ] **Step 5: Commit**（提权执行）

```bash
git add backend .gitignore
git commit -m "feat: 项目骨架、配置与健康检查"
```

---
### Task 2: 数据库层、元数据模型与 Alembic 迁移

**Files:**
- Create: `backend/app/db.py`, `backend/app/models/__init__.py`, `backend/app/models/base.py`, `backend/app/models/sync.py`, `backend/alembic.ini`, `backend/alembic/`(alembic init 生成后改 env.py), `backend/tests/conftest.py`, `backend/tests/integration/test_models.py`

**Interfaces:**
- Produces:
  - `make_engine(url: str, **kwargs) -> Engine`
  - `make_session_factory(engine: Engine) -> sessionmaker`
  - `Base: DeclarativeBase`
  - `SyncTask`（字段见下）、`SyncHistory`（字段见下）
  - conftest fixtures：`pg_engine`、`pg_session_factory`（docker PG 不可达则 skip 整个模块）

`backend/app/db.py`：

```python
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def make_engine(url: str, **kwargs) -> Engine:
    return create_engine(url, pool_pre_ping=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
```

`backend/app/models/base.py`：

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`backend/app/models/sync.py`：

```python
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SyncTask(Base):
    __tablename__ = "sync_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_table: Mapped[str] = mapped_column(String(200))
    target_table: Mapped[str] = mapped_column(String(200))  # 形如 ods.sales_order
    pk_columns: Mapped[list] = mapped_column(JSON)  # ["id"]
    columns: Mapped[list] = mapped_column(JSON)  # 同步字段清单（含主键与增量字段）
    incremental_field: Mapped[str | None] = mapped_column(String(100), nullable=True)  # None=全量比对
    cron: Mapped[str] = mapped_column(String(50))  # 5 段 cron
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("sync_tasks.id"))
    trigger: Mapped[str] = mapped_column(String(10))  # cron / manual
    status: Mapped[str] = mapped_column(String(10))  # success / failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_pulled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_written: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`backend/app/models/__init__.py`：

```python
from app.models.base import Base
from app.models.sync import SyncHistory, SyncTask

__all__ = ["Base", "SyncTask", "SyncHistory"]
```

`backend/tests/conftest.py`：

```python
import os

import pytest
from sqlalchemy import text

from app.db import make_engine, make_session_factory

PG_URL = os.environ.get(
    "BISQL_TEST_PG_URL", "postgresql+psycopg://bisql:bisql@localhost:55432/bisql"
)


def _reachable(url: str) -> bool:
    try:
        make_engine(url).connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.usefixtures()


@pytest.fixture(scope="session")
def pg_engine():
    if not _reachable(PG_URL):
        pytest.skip("PostgreSQL(docker) 不可达：先 docker compose up -d postgres")
    engine = make_engine(PG_URL)
    from app.models import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def pg_session_factory(pg_engine):
    return make_session_factory(pg_engine)
```

`backend/tests/integration/test_models.py`：

```python
from sqlalchemy import inspect

from app.models import SyncHistory, SyncTask


def test_metadata_tables_created(pg_engine):
    names = inspect(pg_engine).get_table_names()
    assert "sync_tasks" in names
    assert "sync_history" in names


def test_insert_task_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        task = SyncTask(
            name="t-roundtrip",
            source_table="sales_order",
            target_table="ods.sales_order",
            pk_columns=["id"],
            columns=["id", "region", "amount", "update_time"],
            incremental_field="update_time",
            cron="*/15 * * * *",
        )
        s.add(task)
        s.commit()
        s.expire_all()
        loaded = s.query(SyncTask).filter_by(name="t-roundtrip").one()
        assert loaded.pk_columns == ["id"]
        assert loaded.watermark is None
        assert s.query(SyncHistory).count() == 0
        s.delete(loaded)
        s.commit()
```

- [ ] **Step 1: 先写上面测试文件**，运行 `python -m pytest tests/integration/test_models.py -v`；Expected: SKIP（PG 不可达）
- [ ] **Step 2: 启动 PG** — 本机已配置原生 PostgreSQL 16.15（`localhost:55432`，用户/密码/库均为 `bisql`），直接使用即可；换机器时可用 `docker compose up -d postgres` 或 `docker run -d --name bisql-pg -e POSTGRES_USER=bisql -e POSTGRES_PASSWORD=bisql -e POSTGRES_DB=bisql -p 55432:5432 postgres:16`；Expected: 可连接（`psql -h localhost -p 55432 -U bisql -d bisql -c "select 1"`）
- [ ] **Step 3: 安装 alembic 并初始化** — Run: `alembic init alembic`（在 backend/ 下）；编辑 `alembic/env.py`：

```python
# 文件顶部追加
from app.models import Base
from app.settings import get_settings

# offline/online 共用：把 target_metadata 与 url 换掉
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

- [ ] **Step 4: 生成并执行迁移**

Run: `alembic revision --autogenerate -m "sync tasks and history"` 然后 `alembic upgrade head`；Expected: `sync_tasks`、`sync_history` 创建成功。

- [ ] **Step 5: 跑集成测试** — Run: `python -m pytest tests/integration/test_models.py -v`；Expected: 2 passed

- [ ] **Step 6: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: 元数据模型、数据库层与 alembic 迁移"
```

---

### Task 3: ERP 只读访问层（增量/全量流式拉取）

**Files:**
- Create: `backend/app/sync/__init__.py`, `backend/app/sync/spec.py`, `backend/app/sync/puller.py`, `backend/tests/unit/test_puller.py`

**Interfaces:**
- Produces:
  - `SyncSpec(source_table: str, columns: list[str], pk_columns: list[str], incremental_field: str | None)`（frozen dataclass）
  - `spec_from_task(task: SyncTask) -> SyncSpec`
  - `quote_ident(name: str) -> str`（校验失败抛 ValueError）
  - `quote_qualified(name: str) -> str`（`schema.table` 逐段校验并拼接，单段等价 quote_ident）
  - `make_erp_engine(url: str, query_timeout: int) -> Engine`
  - `iter_rows(erp_engine, spec, watermark, batch_size) -> Iterator[list[dict]]`（每批是 `dict[列名, 值]` 列表，按主键有序）

`backend/app/sync/spec.py`：

```python
import re
from dataclasses import dataclass

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return name


def quote_qualified(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 2:
        return f"{quote_ident(parts[0])}.{quote_ident(parts[1])}"
    return quote_ident(name)


@dataclass(frozen=True)
class SyncSpec:
    source_table: str
    columns: list[str]
    pk_columns: list[str]
    incremental_field: str | None


def spec_from_task(task) -> SyncSpec:
    return SyncSpec(
        source_table=task.source_table,
        columns=list(task.columns),
        pk_columns=list(task.pk_columns),
        incremental_field=task.incremental_field,
    )
```

`backend/app/sync/puller.py`：

```python
from collections.abc import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import SyncSpec, quote_ident


def make_erp_engine(url: str, query_timeout: int) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "read_timeout": query_timeout},
    )


def build_select_sql(spec: SyncSpec, watermark) -> tuple[str, dict]:
    cols = ", ".join(quote_ident(c) for c in spec.columns)
    table = quote_ident(spec.source_table)
    params: dict = {}
    where = ""
    if watermark is not None and spec.incremental_field is not None:
        where = f" WHERE {quote_ident(spec.incremental_field)} > :watermark"
        params["watermark"] = watermark
    order = " ORDER BY " + ", ".join(quote_ident(c) for c in spec.pk_columns)
    return f"SELECT {cols} FROM {table}{where}{order}", params


def iter_rows(
    erp_engine: Engine, spec: SyncSpec, watermark, batch_size: int
) -> Iterator[list[dict]]:
    sql, params = build_select_sql(spec, watermark)
    with erp_engine.connect() as conn:
        result = conn.execution_options(stream_results=True, yield_per=batch_size).execute(
            text(sql), params
        )
        while batch := result.fetchmany(batch_size):
            yield [dict(row._mapping) for row in batch]
```

`backend/tests/unit/test_puller.py`（用 sqlite 验证 SQL 构造与流式分批）：

```python
import sqlalchemy as sa

from app.sync.puller import build_select_sql, iter_rows
from app.sync.spec import SyncSpec

SPEC = SyncSpec(
    source_table="sales_order",
    columns=["id", "region", "amount"],
    pk_columns=["id"],
    incremental_field=None,
)


def test_quote_ident_rejects_injection():
    from app.sync.puller import quote_ident
    import pytest

    with pytest.raises(ValueError):
        quote_ident("id; DROP TABLE x")
    with pytest.raises(ValueError):
        quote_ident("1abc")


def test_build_select_sql_with_watermark():
    from datetime import datetime

    spec = SyncSpec(
        source_table="sales_order",
        columns=["id", "region", "amount", "update_time"],
        pk_columns=["id"],
        incremental_field="update_time",
    )
    sql, params = build_select_sql(spec, datetime(2026, 9, 1))
    assert "WHERE update_time > :watermark" in sql
    assert "ORDER BY id" in sql
    assert params["watermark"] == datetime(2026, 9, 1)


def test_iter_rows_batches():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE sales_order (id INT, region TEXT, amount REAL)"))
        conn.execute(
            sa.text("INSERT INTO sales_order VALUES (1,'华东',10.0),(2,'华北',20.0),(3,'华南',30.0)")
        )
    batches = list(iter_rows(engine, SPEC, None, batch_size=2))
    assert [len(b) for b in batches] == [2, 1]
    assert batches[0][0] == {"id": 1, "region": "华东", "amount": 10.0}
```

- [ ] **Step 1: 写失败测试** → Run: `python -m pytest tests/unit/test_puller.py -v`；Expected: FAIL（模块不存在）
- [ ] **Step 2: 实现 spec.py / puller.py**（代码如上，`__init__.py` 为空）
- [ ] **Step 3: 跑测试** — Run: `python -m pytest tests/unit/test_puller.py -v`；Expected: 4 passed
- [ ] **Step 4: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: ERP 只读拉取层与标识符校验"
```

---

### Task 4: ods 建表、upsert 写入与同步主流程

**Files:**
- Create: `backend/app/sync/ods.py`, `backend/app/sync/writer.py`, `backend/app/sync/engine.py`
- Test: `backend/tests/unit/test_writer.py`, `backend/tests/integration/test_engine.py`

**Interfaces:**
- Consumes: Task 2 的 `SyncTask/SyncHistory`、`make_session_factory`；Task 3 的 `SyncSpec/iter_rows`
- Produces:
  - `ensure_ods_table(erp_engine, pg_engine, spec, target_table) -> None`（不存在则按 ERP 列类型建表）
  - `mysql_type_to_pg(mysql_type: str) -> str`
  - `upsert_rows(pg_engine, target_table, rows: list[dict], pk_columns: list[str]) -> int`（返回写入行数）
  - `run_sync(session, erp_engine, pg_engine, task, trigger: str, batch_size: int = 5000) -> dict`（返回 `{"status","rows_pulled","rows_written","error"}`，内部写 SyncHistory 并推进 watermark）

`backend/app/sync/ods.py`：

```python
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import SyncSpec, quote_ident

_TYPE_MAP = [
    ("bigint", "bigint"),
    ("int", "integer"),
    ("smallint", "smallint"),
    ("tinyint", "smallint"),
    ("decimal", "numeric"),
    ("numeric", "numeric"),
    ("double", "double precision"),
    ("float", "double precision"),
    ("datetime", "timestamptz"),
    ("timestamp", "timestamptz"),
    ("date", "date"),
    ("char", "text"),
    ("text", "text"),
    ("blob", "bytea"),
    ("json", "jsonb"),
]


def mysql_type_to_pg(mysql_type: str) -> str:
    t = mysql_type.lower()
    for prefix, pg in _TYPE_MAP:
        if t.startswith(prefix):
            return pg
    return "text"


def _pg_type_from_value(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, float):
        return "double precision"
    if isinstance(value, Decimal):
        return "numeric"
    if isinstance(value, datetime):
        return "timestamptz"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "bytea"
    if isinstance(value, (dict, list)):
        return "jsonb"
    return "text"


def _column_types(erp_engine: Engine, spec: SyncSpec) -> dict[str, str]:
    """MySQL 走 information_schema；其他方言（测试用 sqlite）取一行样本推断。"""
    if erp_engine.dialect.name == "mysql":
        with erp_engine.connect() as erp_conn:
            rows = erp_conn.execute(
                text(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                    "ORDER BY ORDINAL_POSITION"
                ),
                {"t": spec.source_table},
            ).all()
        mapped = {name: mysql_type_to_pg(dtype or "text") for name, dtype in rows}
        if all(c in mapped for c in spec.columns):
            return {c: mapped[c] for c in spec.columns}
    cols = ", ".join(quote_ident(c) for c in spec.columns)
    with erp_engine.connect() as erp_conn:
        sample = (
            erp_conn.execute(
                text(f"SELECT {cols} FROM {quote_ident(spec.source_table)} LIMIT 1")
            )
            .mappings()
            .first()
        )
    return {
        c: _pg_type_from_value(sample.get(c) if sample else None) for c in spec.columns
    }


def ensure_ods_table(erp_engine: Engine, pg_engine: Engine, spec: SyncSpec, target_table: str) -> None:
    schema, _, table = target_table.rpartition(".")
    schema = schema or "ods"
    types = _column_types(erp_engine, spec)
    col_defs = [f"{quote_ident(c)} {types[c]}" for c in spec.columns]
    pk = ", ".join(quote_ident(c) for c in spec.pk_columns)
    ddl = (
        f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema)}; "
        f"CREATE TABLE IF NOT EXISTS {quote_ident(schema)}.{quote_ident(table)} "
        f"({', '.join(col_defs)}, _synced_at timestamptz NOT NULL DEFAULT now(), "
        f"PRIMARY KEY ({pk}))"
    )
    with pg_engine.begin() as pg_conn:
        pg_conn.execute(text(ddl))
```

`backend/app/sync/writer.py`：

```python
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import quote_ident, quote_qualified


def upsert_rows(pg_engine: Engine, target_table: str, rows: list[dict], pk_columns: list[str]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    all_cols = cols + ["_synced_at"]
    col_sql = ", ".join(quote_ident(c) for c in all_cols)
    pk_sql = ", ".join(quote_ident(c) for c in pk_columns)
    update_sql = ", ".join(
        f"{quote_ident(c)} = EXCLUDED.{quote_ident(c)}" for c in cols if c not in pk_columns
    )
    if update_sql:
        conflict = f"ON CONFLICT ({pk_sql}) DO UPDATE SET {update_sql}, _synced_at = now()"
    else:
        conflict = f"ON CONFLICT ({pk_sql}) DO NOTHING"
    sql = text(
        f"INSERT INTO {quote_qualified(target_table)} ({col_sql}) VALUES (:{', :'.join(all_cols)}) "
        f"{conflict}"
    )
    params = [{**row, "_synced_at": datetime.now(timezone.utc)} for row in rows]
    with pg_engine.begin() as conn:
        conn.execute(sql, params)
    return len(rows)
```

`backend/app/sync/engine.py`：

```python
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import SyncHistory
from app.sync.ods import ensure_ods_table
from app.sync.puller import iter_rows
from app.sync.spec import quote_qualified, spec_from_task
from app.sync.writer import upsert_rows


def _row_hash(row: dict) -> str:
    return hashlib.sha1(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def run_sync(
    session: Session,
    erp_engine: Engine,
    pg_engine: Engine,
    task,
    trigger: str,
    batch_size: int = 5000,
) -> dict:
    spec = spec_from_task(task)
    history = SyncHistory(task_id=task.id, trigger=trigger, status="failed")
    session.add(history)
    session.commit()
    result = {"status": "failed", "rows_pulled": 0, "rows_written": 0, "error": None}
    try:
        ensure_ods_table(erp_engine, pg_engine, spec, task.target_table)
        pulled = written = 0
        new_watermark = task.watermark
        if spec.incremental_field is not None:
            for batch in iter_rows(erp_engine, spec, task.watermark, batch_size):
                written += upsert_rows(pg_engine, task.target_table, batch, spec.pk_columns)
                pulled += len(batch)
                batch_max = max(b[spec.incremental_field] for b in batch)
                if new_watermark is None or batch_max > new_watermark:
                    new_watermark = batch_max
        else:
            existing: dict = {}
            with pg_engine.connect() as conn:
                for row in conn.execute(text(f"SELECT * FROM {quote_qualified(task.target_table)}")):
                    d = dict(row._mapping)
                    d.pop("_synced_at", None)
                    key = tuple(d[c] for c in spec.pk_columns)
                    existing[key] = _row_hash(d)
            for batch in iter_rows(erp_engine, spec, None, batch_size):
                to_write = []
                for row in batch:
                    key = tuple(row[c] for c in spec.pk_columns)
                    if existing.get(key) != _row_hash(row):
                        to_write.append(row)
                if to_write:
                    written += upsert_rows(pg_engine, task.target_table, to_write, spec.pk_columns)
                pulled += len(batch)
        if spec.incremental_field is not None and new_watermark is not None:
            if getattr(new_watermark, "tzinfo", None) is None:
                new_watermark = new_watermark.replace(tzinfo=timezone.utc)
            task.watermark = new_watermark
        history.status = "success"
        result.update(status="success", rows_pulled=pulled, rows_written=written)
    except Exception as exc:  # noqa: BLE001 —— 记录后向上抛，交由重试层处理
        history.error = str(exc)[:2000]
        history.finished_at = datetime.now(timezone.utc)
        result["error"] = str(exc)[:2000]
        session.commit()
        raise
    history.rows_pulled = pulled
    history.rows_written = written
    history.finished_at = datetime.now(timezone.utc)
    session.commit()
    return result
```

`backend/tests/unit/test_writer.py`（sqlite 不支持 ON CONFLICT 语法差异，写 PG 语法用 sqlite 不可行 → 该测试放 integration）：

```python
# tests/integration/test_engine.py
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.models import SyncTask
from app.sync.engine import run_sync


@pytest.fixture
def erp_sqlite():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE sales_order (id INT, region TEXT, amount REAL, update_time TEXT)"))
        conn.execute(sa.text("INSERT INTO sales_order VALUES (1,'华东',10.0,'2026-09-01 00:00:00')"))
        conn.execute(sa.text("INSERT INTO sales_order VALUES (2,'华北',20.0,'2026-09-02 00:00:00')"))
    return engine


@pytest.fixture
def pg_for_ods(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text("DROP TABLE IF EXISTS ods.sales_order_test"))
    return pg_engine


def _make_task(session):
    task = SyncTask(
        name="t-engine",
        source_table="sales_order",
        target_table="ods.sales_order_test",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field=None,  # 全量比对，避开跨库类型/时区差异
        cron="*/15 * * * *",
    )
    session.add(task)
    session.commit()
    return task


def test_full_compare_sync(pg_session_factory, erp_sqlite, pg_for_ods):
    with pg_session_factory() as session:
        task = _make_task(session)
        r1 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r1["status"] == "success" and r1["rows_written"] == 2
        # 重复跑：无差异，不写行
        r2 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r2["rows_written"] == 0
        # 变更一行 + 新增一行
        with erp_sqlite.begin() as conn:
            conn.execute(sa.text("UPDATE sales_order SET amount=99.0 WHERE id=1"))
            conn.execute(sa.text("INSERT INTO sales_order VALUES (3,'华南',30.0,'2026-09-03 00:00:00')"))
        r3 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r3["rows_written"] == 2
        with pg_for_ods.connect() as conn:
            amount = conn.execute(
                sa.text("SELECT amount FROM ods.sales_order_test WHERE id=1")
            ).scalar_one()
        assert float(amount) == 99.0
```

- [ ] **Step 1: 写失败测试**（两个测试文件）→ Run: `python -m pytest tests/integration/test_engine.py -v`；Expected: FAIL
- [ ] **Step 2: 实现 ods.py / writer.py / engine.py**
- [ ] **Step 3: 跑测试** — Run: `python -m pytest tests/integration/test_engine.py tests/unit -v`；Expected: 全部 passed（engine 测试依赖 Task 2 的 PG fixture）

- 注：`ensure_ods_table` 对 ERP 列类型依赖 `information_schema`，integration 测试用 sqlite 时类型为空 → 全量比对测试用 `mysql_type_to_pg('text')` 兜底，不影响断言；真实 ERP（MySQL）在 Task 10 验证类型映射。

- [ ] **Step 4: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: ods 建表、upsert 与同步主流程"
```

---

### Task 5: 失败重试与 webhook 告警

**Files:**
- Create: `backend/app/sync/retry.py`, `backend/app/sync/alert.py`
- Test: `backend/tests/unit/test_retry.py`, `backend/tests/unit/test_alert.py`

**Interfaces:**
- Produces:
  - `run_with_retry(fn, attempts=3, base_delay=5.0, sleep=time.sleep)`：失败重试，第 i 次重试前 sleep `base_delay * 2**(i-1)`；耗尽后抛最后一次异常
  - `send_alert(webhook_url, title, detail, transport=None) -> bool`：POST JSON `{"title","detail"}`；url 为空或任何异常返回 False

`backend/app/sync/retry.py`：

```python
import time
from collections.abc import Callable, TypeVar

T = TypeVar("T")


def run_with_retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < attempts - 1:
                sleep(base_delay * (2**i))
    assert last_exc is not None
    raise last_exc
```

`backend/app/sync/alert.py`：

```python
import httpx


def send_alert(
    webhook_url: str,
    title: str,
    detail: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if not webhook_url:
        return False
    try:
        kwargs: dict = {"timeout": 10.0}
        if transport is not None:
            kwargs["transport"] = transport
        with httpx.Client(**kwargs) as client:
            resp = client.post(webhook_url, json={"title": title, "detail": detail})
            return resp.status_code < 300
    except Exception:  # noqa: BLE001 告警失败不影响主流程
        return False
```

`backend/tests/unit/test_retry.py`：

```python
import pytest

from app.sync.retry import run_with_retry


def test_success_first_try():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert run_with_retry(fn, attempts=3, base_delay=0.0) == "ok"
    assert len(calls) == 1


def test_retry_then_success_with_exponential_backoff():
    state = {"n": 0}
    sleeps: list[float] = []

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("flaky")
        return "done"

    result = run_with_retry(fn, attempts=3, base_delay=2.0, sleep=sleeps.append)
    assert result == "done"
    assert sleeps == [2.0, 4.0]


def test_exhausted_raises_last_error():
    def fn():
        raise ValueError("always")

    with pytest.raises(ValueError, match="always"):
        run_with_retry(fn, attempts=2, base_delay=0.0)
```

`backend/tests/unit/test_alert.py`：

```python
import httpx

from app.sync.alert import send_alert


def test_empty_url_returns_false():
    assert send_alert("", "t", "d") is False


def test_posts_json_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200)

    ok = send_alert("http://hook.local/x", "同步失败", "task 1", transport=httpx.MockTransport(handler))
    assert ok is True
    assert seen["url"] == "http://hook.local/x"
    assert "同步失败" in seen["body"].decode("utf-8")


def test_transport_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert send_alert("http://hook.local/x", "t", "d", transport=httpx.MockTransport(handler)) is False
```

- [ ] **Step 1: 写失败测试** → `python -m pytest tests/unit/test_retry.py tests/unit/test_alert.py -v`；Expected: FAIL
- [ ] **Step 2: 实现 retry.py / alert.py** → 跑同一命令；Expected: 6 passed
- [ ] **Step 3: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: 同步重试与 webhook 告警"
```

---

### Task 6: APScheduler 调度集成

**Files:**
- Create: `backend/app/scheduler.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_scheduler.py`

**Interfaces:**
- Consumes: Task 4 `run_sync`、Task 5 `run_with_retry/send_alert`、Task 2 `make_engine/make_session_factory`、Task 1 `get_settings`
- Produces:
  - `job_context() -> (session_factory, erp_engine, pg_engine, settings)`
  - `execute_task(task_id: int, trigger: str) -> None`：开上下文 → 跳过不存在/停用任务 → `run_with_retry(run_sync)` → 最终失败发 webhook 告警；finally 释放引擎
  - `refresh_jobs(scheduler, session_factory) -> None`：enabled 任务注册为 cron job（id=`sync_{id}`，replace_existing），disabled 任务移除

`backend/app/scheduler.py`：

```python
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models import SyncTask
from app.settings import get_settings
from app.sync.alert import send_alert
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry

logger = logging.getLogger(__name__)


def job_context():
    from app.db import make_engine, make_session_factory
    from app.sync.puller import make_erp_engine

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    erp_engine = make_erp_engine(settings.erp_database_url, settings.erp_query_timeout_seconds)
    session_factory = make_session_factory(pg_engine)
    return session_factory, erp_engine, pg_engine, settings


def execute_task(task_id: int, trigger: str) -> None:
    session_factory, erp_engine, pg_engine, settings = job_context()
    try:
        with session_factory() as session:
            task = session.get(SyncTask, task_id)
            if task is None or not task.enabled:
                return
            try:
                run_with_retry(
                    lambda: run_sync(
                        session,
                        erp_engine,
                        pg_engine,
                        task,
                        trigger,
                        batch_size=settings.sync_batch_size,
                    ),
                    attempts=settings.sync_retry_attempts,
                    base_delay=settings.sync_retry_base_delay,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("sync task %s failed: %s", task_id, exc)
                send_alert(
                    settings.alert_webhook_url,
                    "同步任务失败",
                    f"task_id={task_id} name={task.name} error={exc}",
                )
    finally:
        erp_engine.dispose()
        pg_engine.dispose()


def refresh_jobs(scheduler: BackgroundScheduler, session_factory) -> None:
    with session_factory() as session:
        tasks = session.query(SyncTask).all()
    enabled_ids: set[int] = set()
    for task in tasks:
        if not task.enabled:
            continue
        enabled_ids.add(task.id)
        scheduler.add_job(
            execute_task,
            CronTrigger.from_crontab(task.cron, timezone="Asia/Hong_Kong"),
            id=f"sync_{task.id}",
            args=[task.id, "cron"],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("sync_") and int(job.id.removeprefix("sync_")) not in enabled_ids:
            job.remove()
```

`backend/app/main.py` 改为（同时把引擎与会话放进 `app.state`，Task 7 直接复用）：

```python
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
    erp_engine = make_erp_engine(settings.erp_database_url, settings.erp_query_timeout_seconds)
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
    app.include_router(health_router, prefix="/api/v1")
    return app
```

`backend/tests/unit/test_scheduler.py`：

```python
from apscheduler.schedulers.background import BackgroundScheduler

from app.db import make_engine, make_session_factory
from app.models import SyncTask
from app.scheduler import execute_task, refresh_jobs
from tests.conftest import PG_URL


def _task(name, enabled, cron="0 8 * * *"):
    return SyncTask(
        name=name,
        source_table="sales_order",
        target_table="ods.sales_order",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field="update_time",
        cron=cron,
        enabled=enabled,
    )


def test_refresh_jobs_registers_enabled_and_removes_disabled(pg_session_factory):
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.start()
    try:
        with pg_session_factory() as s:
            on = _task("sch-on", enabled=True)
            off = _task("sch-off", enabled=False, cron="0 9 * * *")
            s.add_all([on, off])
            s.commit()
            refresh_jobs(scheduler, pg_session_factory)
            ids = {j.id for j in scheduler.get_jobs()}
            assert f"sync_{on.id}" in ids
            assert f"sync_{off.id}" not in ids
            s.query(SyncTask).filter(SyncTask.id.in_([on.id, off.id])).delete(synchronize_session=False)
            s.commit()
    finally:
        scheduler.shutdown(wait=False)


def test_execute_task_disabled_is_noop(pg_session_factory, monkeypatch):
    with pg_session_factory() as s:
        t = _task("sch-noop", enabled=False)
        s.add(t)
        s.commit()
        tid = t.id

    class DummyEngine:
        def dispose(self):
            pass

    class FakeSettings:
        sync_retry_attempts = 1
        sync_retry_base_delay = 0.0
        sync_batch_size = 5000
        alert_webhook_url = ""

    local_pg = make_engine(PG_URL)
    local_sf = make_session_factory(local_pg)
    monkeypatch.setattr(
        "app.scheduler.job_context",
        lambda: (local_sf, DummyEngine(), local_pg, FakeSettings()),
    )

    def fail_run_sync(*args, **kwargs):
        raise AssertionError("停用任务不应执行同步")

    monkeypatch.setattr("app.scheduler.run_sync", fail_run_sync)
    execute_task(tid, "manual")  # 不抛异常即通过

    with pg_session_factory() as s:
        s.query(SyncTask).filter_by(id=tid).delete(synchronize_session=False)
        s.commit()
```

- [ ] **Step 1: 写失败测试** → `python -m pytest tests/unit/test_scheduler.py -v`；Expected: FAIL
- [ ] **Step 2: 实现 scheduler.py 与 main.py lifespan** → 跑测试；Expected: 2 passed
- [ ] **Step 3: 全量回归** → `python -m pytest -v`；Expected: 全部 passed
- [ ] **Step 4: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: APScheduler 调度集成与告警挂钩"
```

---

### Task 7: 同步监控 API

**Files:**
- Create: `backend/app/api/deps.py`, `backend/app/api/sync_api.py`
- Modify: `backend/app/main.py`（注册路由）
- Test: `backend/tests/unit/test_sync_api.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/sync/tasks` → 任务列表（id/name/source_table/target_table/cron/enabled/watermark）
  - `POST /api/v1/sync/tasks/{task_id}/run` → 手动同步；404=任务不存在；同步失败返回 200 + `status=failed` 且已告警（P1 约定）
  - `GET /api/v1/sync/tasks/{task_id}/history?limit=20` → 历史倒序
  - `Context(session_factory, erp_engine, pg_engine, settings)` 与 `get_ctx(request)` 依赖（测试可 override）

`backend/app/api/deps.py`：

```python
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@dataclass
class Context:
    session_factory: sessionmaker
    erp_engine: Engine
    pg_engine: Engine
    settings: object


def get_ctx(request: Request) -> Context:
    return Context(
        session_factory=request.app.state.session_factory,
        erp_engine=request.app.state.erp_engine,
        pg_engine=request.app.state.pg_engine,
        settings=request.app.state.settings,
    )
```

`backend/app/api/sync_api.py`：

```python
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import Context, get_ctx
from app.models import SyncHistory, SyncTask
from app.sync.alert import send_alert
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/tasks")
def list_tasks(ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        tasks = session.query(SyncTask).order_by(SyncTask.id).all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "source_table": t.source_table,
                "target_table": t.target_table,
                "cron": t.cron,
                "enabled": t.enabled,
                "watermark": t.watermark.isoformat() if t.watermark else None,
            }
            for t in tasks
        ]


@router.post("/tasks/{task_id}/run")
def run_task(task_id: int, ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        task = session.get(SyncTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        try:
            result = run_with_retry(
                lambda: run_sync(
                    session,
                    ctx.erp_engine,
                    ctx.pg_engine,
                    task,
                    trigger="manual",
                    batch_size=ctx.settings.sync_batch_size,
                ),
                attempts=ctx.settings.sync_retry_attempts,
                base_delay=ctx.settings.sync_retry_base_delay,
            )
        except Exception as exc:  # noqa: BLE001
            send_alert(
                ctx.settings.alert_webhook_url,
                "同步任务失败",
                f"task_id={task_id} error={exc}",
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "rows_pulled": 0,
                "rows_written": 0,
                "error": str(exc)[:500],
            }
        return {"task_id": task_id, **result}


@router.get("/tasks/{task_id}/history")
def task_history(task_id: int, limit: int = Query(20, le=100), ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        if session.get(SyncTask, task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        rows = (
            session.query(SyncHistory)
            .filter(SyncHistory.task_id == task_id)
            .order_by(SyncHistory.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": h.id,
                "trigger": h.trigger,
                "status": h.status,
                "started_at": h.started_at.isoformat() if h.started_at else None,
                "finished_at": h.finished_at.isoformat() if h.finished_at else None,
                "rows_pulled": h.rows_pulled,
                "rows_written": h.rows_written,
                "error": h.error,
            }
            for h in rows
        ]
```

`backend/app/main.py` 的 `create_app()` 追加一行：

```python
    from app.api.sync_api import router as sync_router

    app.include_router(sync_router, prefix="/api/v1")
```

`backend/tests/unit/test_sync_api.py`：

```python
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.main import create_app
from app.models import SyncHistory, SyncTask


class FakeSettings:
    sync_retry_attempts = 1
    sync_retry_base_delay = 0.0
    sync_batch_size = 5000
    alert_webhook_url = ""


@pytest.fixture
def api_client(pg_session_factory, pg_engine):
    app = create_app()

    def override_ctx(request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=pg_engine,
            settings=FakeSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx
    return TestClient(app)


def test_list_and_history(api_client, pg_session_factory):
    with pg_session_factory() as s:
        t = SyncTask(
            name="api-t",
            source_table="sales_order",
            target_table="ods.api_t",
            pk_columns=["id"],
            columns=["id"],
            incremental_field=None,
            cron="0 8 * * *",
        )
        s.add(t)
        s.commit()
        s.add(
            SyncHistory(
                task_id=t.id,
                trigger="manual",
                status="success",
                finished_at=datetime.now(timezone.utc),
                rows_pulled=3,
                rows_written=3,
            )
        )
        s.commit()
        tid = t.id

    resp = api_client.get("/api/v1/sync/tasks")
    assert resp.status_code == 200
    assert any(item["id"] == tid for item in resp.json())

    resp = api_client.get(f"/api/v1/sync/tasks/{tid}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["status"] == "success"
    assert body[0]["rows_pulled"] == 3

    assert api_client.get("/api/v1/sync/tasks/999999/history").status_code == 404

    with pg_session_factory() as s:
        s.query(SyncHistory).filter_by(task_id=tid).delete()
        s.query(SyncTask).filter_by(id=tid).delete()
        s.commit()
```

- [ ] **Step 1: 写失败测试** → `python -m pytest tests/unit/test_sync_api.py -v`；Expected: FAIL
- [ ] **Step 2: 实现 deps.py / sync_api.py，改 main.py** → 跑测试；Expected: 1 passed
- [ ] **Step 3: 全量回归** → `python -m pytest -v`；Expected: 全部 passed
- [ ] **Step 4: Commit**（提权执行）

```bash
git add backend
git commit -m "feat: 同步监控 API（任务/手动触发/历史）"
```

---

### Task 8: Docker Compose 本地环境与配置样例

**Files:**
- Create: `docker-compose.yml`, `backend/.env.example`, `ops/mysql-init/01-demo.sql`, `README.md`

**Interfaces:**
- Produces: `docker compose up -d` 后本地 PG `localhost:55432`（bisql/bisql）、ERP MySQL `localhost:53306`（root/root，库 erp，含 `sales_order` 演示数据）

`docker-compose.yml`：

```yaml
services:
  postgres:
    image: postgres:16
    container_name: bisql-pg
    environment:
      POSTGRES_USER: bisql
      POSTGRES_PASSWORD: bisql
      POSTGRES_DB: bisql
    ports:
      - "55432:5432"
    volumes:
      - bisql_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bisql"]
      interval: 5s
      timeout: 3s
      retries: 10

  mysql-erp:
    image: mysql:8
    container_name: bisql-mysql-erp
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: erp
    ports:
      - "53306:3306"
    volumes:
      - bisql_mysql_data:/var/lib/mysql
      - ./ops/mysql-init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-proot"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  bisql_pgdata:
  bisql_mysql_data:
```

> 注：`ops/mysql-init` 只在 MySQL 数据卷首次创建时执行；若卷已存在，先 `docker compose down -v` 再 `up -d` 重新初始化演示表。

`ops/mysql-init/01-demo.sql`：

```sql
CREATE TABLE IF NOT EXISTS sales_order (
  id INT PRIMARY KEY,
  region VARCHAR(50) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  update_time DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO sales_order VALUES
  (1, '华东', 100.50, '2026-09-08 08:00:00'),
  (2, '华北', 200.00, '2026-09-08 09:00:00'),
  (3, '华东', 300.25, '2026-09-09 10:00:00');
```

`backend/.env.example`：

```env
# 中台 PostgreSQL（docker compose 提供）
BISQL_DATABASE_URL=postgresql+psycopg://bisql:bisql@localhost:55432/bisql
# ERP MySQL 只读账号（当前可先用 compose 的本地演示库，上线换真实只读账号）
BISQL_ERP_DATABASE_URL=mysql+pymysql://root:root@localhost:53306/erp?charset=utf8mb4
BISQL_ERP_QUERY_TIMEOUT_SECONDS=30
BISQL_SYNC_BATCH_SIZE=5000
BISQL_SYNC_RETRY_ATTEMPTS=3
BISQL_SYNC_RETRY_BASE_DELAY=5
# 企业微信/钉钉机器人 webhook（可选）
BISQL_ALERT_WEBHOOK_URL=
```

`README.md`：

```markdown
# BIsql 数据分发中台

设计文档：docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md
当前进度：P1（同步引擎）

## 本地开发

1. 启动依赖（二选一）：本机原生 PostgreSQL 16（`localhost:55432`，默认连接串已指向它）；或 `docker compose up -d`（PG 55432 / ERP MySQL 53306）
2. `cd backend && python -m pip install -e ".[dev]"`
3. `copy .env.example .env`（按需改连接串）
4. `alembic upgrade head`
5. `python -m uvicorn app.main:app --reload`
6. 健康检查：GET http://127.0.0.1:8000/api/v1/health

## 测试

cd backend && python -m pytest -v
```

- [ ] **Step 1: 写全部文件**
- [ ] **Step 2: 验证环境** → `docker compose up -d`；`docker compose ps`；`docker compose exec mysql-erp mysql -uroot -proot erp -e "SELECT COUNT(*) FROM sales_order"`；Expected: 两容器 healthy，计数=3
- [ ] **Step 3: Commit**（提权执行）

```bash
git add docker-compose.yml ops backend/.env.example README.md
git commit -m "chore: 本地 docker 环境与配置样例"
```

---

### Task 9: 端到端验收（增量水位线 + 失败重试 + 告警）

**Files:**
- Modify: `backend/tests/conftest.py`（补 MySQL fixture）
- Create: `backend/tests/e2e/test_p1_acceptance.py`

**Interfaces:**
- Consumes: 前面全部公共接口 + docker PG/MySQL
- Produces: P1 验收证据（自动化覆盖「指定 ERP 表按时进中台，失败有重试告警」）

先给 `backend/tests/conftest.py` 追加：

```python
MYSQL_URL = os.environ.get(
    "BISQL_TEST_MYSQL_URL", "mysql+pymysql://root:root@localhost:53306/erp?charset=utf8mb4"
)


@pytest.fixture(scope="session")
def mysql_erp_engine():
    if not _reachable(MYSQL_URL):
        pytest.skip("ERP MySQL(docker) 不可达：先 docker compose up -d mysql-erp")
    engine = make_engine(MYSQL_URL)
    yield engine
    engine.dispose()
```

`backend/tests/e2e/test_p1_acceptance.py`：

```python
import pytest
import sqlalchemy as sa

from app.models import SyncHistory, SyncTask
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry
from tests.conftest import PG_URL


@pytest.fixture(scope="module")
def erp_mysql(mysql_erp_engine):
    return mysql_erp_engine


class FlakyEngine:
    """前 fail_times 次 connect 抛错，之后透传真实引擎。"""

    def __init__(self, real, fail_times=1):
        self.real = real
        self.fail_times = fail_times
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("erp temporarily down")
        return self.real.connect()

    def dispose(self):
        pass


def _make_task(session):
    task = SyncTask(
        name="e2e-sales-order",
        source_table="sales_order",
        target_table="ods.sales_order_e2e",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field="update_time",
        cron="*/15 * * * *",
    )
    session.add(task)
    session.commit()
    return task


def test_e2e_incremental_watermark_and_retry_alert(
    pg_session_factory, pg_engine, erp_mysql, monkeypatch
):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS ods.sales_order_e2e"))

    with erp_mysql.begin() as conn:
        conn.execute(sa.text("DELETE FROM sales_order WHERE id NOT IN (1, 2, 3)"))
        conn.execute(
            sa.text(
                "INSERT INTO sales_order (id, region, amount, update_time) "
                "VALUES (101, '华东', 999.99, '2026-09-09 12:00:00')"
            )
        )

    with pg_session_factory() as session:
        task = _make_task(session)

        # ① 首次全量进入 ods，水位线推进
        r1 = run_sync(session, erp_mysql, pg_engine, task, trigger="manual")
        assert r1["status"] == "success"
        assert r1["rows_written"] >= 4
        wm1 = task.watermark
        assert wm1 is not None

        # ② 新增一行后只写差异，水位线前进
        with erp_mysql.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO sales_order (id, region, amount, update_time) "
                    "VALUES (102, '华北', 50.00, '2026-09-09 18:00:00')"
                )
            )
        r2 = run_sync(session, erp_mysql, pg_engine, task, trigger="manual")
        assert r2["rows_written"] == 1
        assert task.watermark > wm1

        # ③ 失败重试：首次 connect 失败、第二次成功；失败记录留在 SyncHistory
        flaky = FlakyEngine(erp_mysql, fail_times=1)
        result = run_with_retry(
            lambda: run_sync(session, flaky, pg_engine, task, trigger="manual"),
            attempts=2,
            base_delay=0.0,
            sleep=lambda _: None,
        )
        assert result["status"] == "success"
        assert flaky.calls == 2
        failed = (
            session.query(SyncHistory)
            .filter_by(task_id=task.id, status="failed")
            .order_by(SyncHistory.id.desc())
            .first()
        )
        assert failed is not None and "temporarily down" in (failed.error or "")
        assert task.watermark >= wm1

        # ④ 最终失败触发 webhook 告警（打桩断言）
        from app import scheduler as scheduler_module
        from app.db import make_engine, make_session_factory

        alerts: list = []

        class DeadEngine:
            def connect(self):
                raise RuntimeError("erp down")

            def dispose(self):
                pass

        class FakeSettings:
            sync_retry_attempts = 2
            sync_retry_base_delay = 0.0
            sync_batch_size = 5000
            alert_webhook_url = "http://hook.local/x"

        local_pg = make_engine(PG_URL)
        local_sf = make_session_factory(local_pg)
        monkeypatch.setattr(
            scheduler_module,
            "job_context",
            lambda: (local_sf, DeadEngine(), local_pg, FakeSettings()),
        )
        monkeypatch.setattr(
            scheduler_module, "send_alert", lambda *a, **k: alerts.append(a) or True
        )
        scheduler_module.execute_task(task.id, "manual")
        assert len(alerts) == 1

        # 清理
        session.query(SyncHistory).filter_by(task_id=task.id).delete()
        session.delete(task)
        session.commit()
```

- [ ] **Step 1: 补 conftest 的 MySQL fixture**
- [ ] **Step 2: 写验收测试** → `python -m pytest tests/e2e -v`；Expected: 环境未起时 SKIP，起了必须 PASS
- [ ] **Step 3: 起环境跑通** → `docker compose up -d` 后 `python -m pytest tests/e2e -v`；Expected: PASS
- [ ] **Step 4: 手动验收（推荐）** → 起服务后 `POST /api/v1/sync/tasks/{id}/run`，再 `GET /api/v1/sync/tasks` 观察 watermark 变化
- [ ] **Step 5: Commit + 打标签**（提权执行）

```bash
git add backend
git commit -m "test: P1 端到端验收（增量水位线/失败重试/告警）"
git tag p1-done
```

---

## Self-Review 结论（写计划时已自查）

1. **Spec 覆盖**：P1 = 骨架(T1) + 元数据与迁移(T2) + ERP 只读拉取(T3) + ods 层与同步主流程(T4) + 重试告警(T5) + 定时调度(T6) + 监控 API(T7) + 本地环境(T8) + 验收(T9)。P2+ 的权限/前端/MCP 不在本计划。
2. **占位符**：无 TBD/TODO；代码块均可直接落地。
3. **类型一致性**：`run_sync(session, erp_engine, pg_engine, task, trigger)` 在 T4 定义，T6/T7/T9 调用一致；`run_with_retry(fn, attempts, base_delay, sleep)` 签名一致；`Context` 字段与 `app.state` 赋值一致；`ensure_ods_table(erp_engine, pg_engine, spec, target_table)` 在 T4 定义并被 `run_sync` 调用。
4. **已修正的实现风险**：非 MySQL 测试库改用样本行推断列类型（不再硬查 `information_schema`）；全主键表 upsert 退化为 `DO NOTHING`；失败历史补 `finished_at`。
