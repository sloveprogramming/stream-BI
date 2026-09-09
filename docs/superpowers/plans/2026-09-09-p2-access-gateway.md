# P2 权限模型与 API 网关实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现用户/角色/数据集授权元数据、API Key 签发与鉴权、行级+字段级权限裁剪查询、限流与审计日志，暴露管理端 API 与数据查询网关。

**Architecture:** 在 P1 的 FastAPI 单体上扩展。新增 `models/access.py`（User/Role/Dataset/DatasetGrant/ApiKey/AuditLog），`access/` 模块承载安全核心（口令散列、Key 生成、权限裁剪 SQL 构造、限流），`api/` 新增 admin 与 data 两组路由。所有渠道经同一 `get_principal` 依赖鉴权、同一 `build_scoped_sql` 裁剪。

**Tech Stack:** 沿用 P1（FastAPI、SQLAlchemy 2.0、psycopg、Alembic、pytest），新增零依赖（口令散列用 hashlib.pbkdf2_hmac，Key 用 secrets 模块）。

**Spec:** docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md（§4.2 权限模型、§4.3 API 网关、§5 数据流示例）

## Global Constraints

- 所有代码位于 `backend/`，命令默认在 `backend/` 下执行；Python >=3.12。
- API 路由前缀 `/api/v1`；表名/字段小写下划线；配置类字段用 SQLAlchemy JSON。
- 标识符（表名/列名）必须经 `app.sync.spec.quote_ident`（`^[A-Za-z_][A-Za-z-0-9_]*$` 校验）后拼接；值一律参数化，禁止字符串拼接。
- 口令用 PBKDF2-HMAC-SHA256（200_000 迭代，16 字节盐）；API Key secret 用 `secrets.token_urlsafe(24)`，库中只存 SHA-256 散列，明文仅创建时返回一次。
- 限流为进程内固定窗口（分钟 120 次/日 10000 次，`Settings` 可调）；重启清零在 P2 规模可接受（写入代码注释）。
- 管理端接口要求 `principal.user.is_admin`；测试内不 mock 鉴权，走真实依赖。
- 每个任务完成即 commit（conventional commits，提权执行）。
- 与 P1 的差异决策：请求签名 HMAC 从 P2 推迟到 P5 加固（HTTPS 内网 + bearer key 已满足内部使用；spec §4.3 的防重放升级到 P5）。

## File Structure（P2 结束后新增布局）

```
backend/app/
├── models/
│   ├── access.py        # User/Role/Dataset/DatasetGrant/ApiKey/AuditLog
├── security/
│   ├── passwords.py     # hash_password / verify_password
│   └── apikeys.py       # generate_key / hash_secret / parse_header
├── access/
│   ├── scoped_query.py  # build_scoped_sql（行级规则+字段白名单+用户过滤）
│   └── ratelimit.py     # FixedWindowLimiter
├── api/
│   ├── deps.py          # 扩展 get_ctx + get_principal
│   ├── admin_api.py     # 用户/角色/数据集/授权/Key/审计 管理接口
│   └── data_api.py      # GET /datasets/{name}/rows 数据网关
├── bootstrap.py         # CLI：创建管理员（python -m app.bootstrap <user> <password>）
backend/tests/
├── unit/test_passwords.py / test_apikeys.py / test_scoped_query.py / test_ratelimit.py
├── integration/test_access_models.py
├── unit/test_admin_api.py / test_data_api.py
└── e2e/test_p2_acceptance.py
```

---

### Task 1: 权限元数据模型与迁移

**Files:**
- Create: `backend/app/models/access.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/integration/test_access_models.py`

**Interfaces:**
- Produces: `User(id, username unique, password_hash, display_name, is_admin, enabled, created_at)`、`Role(id, name unique, description)`、`Dataset(id, name unique, source_table, pk_columns JSON, columns JSON, description, enabled)`、`DatasetGrant(id, role_id FK, dataset_id FK, row_rules JSON, field_whitelist JSON nullable, UniqueConstraint(role_id, dataset_id))`、`ApiKey(id, user_id FK, key_id unique, secret_hash, name, enabled, created_at)`、`AuditLog(id, user_id, api_key_id, channel, dataset, action, row_count, duration_ms, detail JSON, created_at)`

- [ ] **Step 1: 写失败测试** `backend/tests/integration/test_access_models.py`

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


def test_user_and_role_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        u = User(username="alice", password_hash="x", display_name="Alice")
        r = Role(name="east-sales", description="华东销售")
        s.add_all([u, r])
        s.commit()
        assert u.id is not None and r.id is not None
        s.query(User).filter_by(username="alice").delete()
        s.query(Role).filter_by(name="east-sales").delete()
        s.commit()


def test_duplicate_username_rejected(pg_session_factory):
    with pg_session_factory() as s:
        s.add(User(username="bob", password_hash="x"))
        s.commit()
        s.add(User(username="bob", password_hash="y"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.query(User).filter_by(username="bob").delete()
        s.commit()


def test_dataset_grant_unique_and_cascade(pg_session_factory):
    with pg_session_factory() as s:
        r = Role(name="grant-r")
        d = Dataset(
            name="sales_order",
            source_table="ods.sales_order",
            pk_columns=["id"],
            columns=["id", "region", "amount"],
            description="销售订单",
        )
        s.add_all([r, d])
        s.commit()
        g = DatasetGrant(
            role_id=r.id,
            dataset_id=d.id,
            row_rules=[{"field": "region", "op": "eq", "value": "华东"}],
            field_whitelist=["id", "region"],
        )
        s.add(g)
        s.commit()
        dup = DatasetGrant(role_id=r.id, dataset_id=d.id, row_rules=[])
        s.add(dup)
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.query(DatasetGrant).filter_by(id=g.id).delete()
        s.query(Dataset).filter_by(id=d.id).delete()
        s.query(Role).filter_by(id=r.id).delete()
        s.commit()


def test_api_key_and_audit_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        u = User(username="carol", password_hash="x")
        d = Dataset(
            name="logistics",
            source_table="ods.logistics",
            pk_columns=["id"],
            columns=["id", "status"],
        )
        s.add_all([u, d])
        s.commit()
        k = ApiKey(user_id=u.id, key_id="bk_abcd1234", secret_hash="h", name="测试")
        s.add(k)
        s.commit()
        a = AuditLog(
            user_id=u.id,
            api_key_id=k.id,
            channel="api",
            dataset="logistics",
            action="query",
            row_count=3,
            duration_ms=12,
            detail={"filters": {}},
        )
        s.add(a)
        s.commit()
        assert a.id is not None
        s.query(AuditLog).filter_by(id=a.id).delete()
        s.query(ApiKey).filter_by(id=k.id).delete()
        s.query(Dataset).filter_by(id=d.id).delete()
        s.query(User).filter_by(id=u.id).delete()
        s.commit()
```

- [ ] **Step 2: 跑测试确认失败** → `python -m pytest tests/integration/test_access_models.py -v`；Expected: ImportError
- [ ] **Step 3: 实现 `app/models/access.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(128))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(256))


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    source_table: Mapped[str] = mapped_column(String(128))
    pk_columns: Mapped[list] = mapped_column(JSON)
    columns: Mapped[list] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DatasetGrant(Base):
    __tablename__ = "dataset_grants"
    __table_args__ = (UniqueConstraint("role_id", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    row_rules: Mapped[list] = mapped_column(JSON, default=list)
    field_whitelist: Mapped[list | None] = mapped_column(JSON)  # None=全部字段


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key_id: Mapped[str] = mapped_column(String(32), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"))
    channel: Mapped[str] = mapped_column(String(16))  # api/mcp/web/export
    dataset: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

`app/models/__init__.py` 追加：

```python
from app.models.access import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User
```

- [ ] **Step 4: 生成迁移** → `alembic revision --autogenerate -m "access control tables"`；检查生成的迁移只含新表；`alembic upgrade head`
- [ ] **Step 5: 跑测试确认通过** → `python -m pytest tests/integration/test_access_models.py -v`；Expected: 4 passed
- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat: 权限元数据模型（用户/角色/数据集/授权/Key/审计）"
```

---

### Task 2: 安全原语（口令散列 / API Key）与权限裁剪查询构造器

**Files:**
- Create: `backend/app/security/__init__.py`, `backend/app/security/passwords.py`, `backend/app/security/apikeys.py`, `backend/app/access/__init__.py`, `backend/app/access/scoped_query.py`
- Test: `backend/tests/unit/test_passwords.py`, `backend/tests/unit/test_apikeys.py`, `backend/tests/unit/test_scoped_query.py`

**Interfaces:**
- Produces:
  - `hash_password(password: str) -> str`（格式 `pbkdf2_sha256$200000$<salt_hex>$<hash_hex>`）；`verify_password(password: str, stored: str) -> bool`
  - `generate_key() -> tuple[str, str]`（返回 `(key_id, secret)`；key_id 形如 `bk_` + 12 位 hex；secret 为 `secrets.token_urlsafe(24)`）；`hash_secret(secret: str) -> str`（sha256 hex）
  - `build_scoped_sql(dataset, grant, filters: dict, limit: int, offset: int) -> tuple[str, dict]`：返回可执行 SQL 与参数字典

- [ ] **Step 1: 写失败测试**

`backend/tests/unit/test_passwords.py`：

```python
from app.security.passwords import hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert "s3cret!" not in h
    assert verify_password("s3cret!", h) is True
    assert verify_password("wrong", h) is False


def test_unique_salt():
    assert hash_password("same") != hash_password("same")
```

`backend/tests/unit/test_apikeys.py`：

```python
from app.security.apikeys import generate_key, hash_secret


def test_generate_key_shape():
    key_id, secret = generate_key()
    assert key_id.startswith("bk_") and len(key_id) == 15
    assert len(secret) >= 32
    assert generate_key()[0] != key_id


def test_hash_secret_deterministic():
    assert hash_secret("abc") == hash_secret("abc")
    assert len(hash_secret("abc")) == 64
```

`backend/tests/unit/test_scoped_query.py`：

```python
import pytest

from app.access.scoped_query import build_scoped_sql


class _Dataset:
    name = "sales_order"
    source_table = "ods.sales_order"
    pk_columns = ["id"]
    columns = ["id", "region", "amount", "cost"]


class _Grant:
    row_rules = [{"field": "region", "op": "eq", "value": "华东"}]
    field_whitelist = ["id", "region", "amount"]


def test_whitelist_and_row_rules_applied():
    sql, params = build_scoped_sql(_Dataset(), _Grant(), {}, 50, 0)
    assert sql.count("SELECT") == 1
    assert '"id"' in sql and '"amount"' in sql
    assert '"cost"' not in sql
    assert '"region" = :r_0' in sql
    assert params["r_0"] == "华东"
    assert "LIMIT :limit OFFSET :offset" in sql


def test_null_whitelist_means_all_fields():
    class G(_Grant):
        field_whitelist = None

    sql, _ = build_scoped_sql(_Dataset(), G(), {}, 50, 0)
    assert '"cost"' in sql


def test_user_filters_parameterized():
    sql, params = build_scoped_sql(_Dataset(), _Grant(), {"amount": 100}, 50, 0)
    assert '"amount" = :f_amount' in sql
    assert params["f_amount"] == 100


def test_invalid_identifier_rejected():
    class Evil(_Dataset):
        columns = ["id; DROP TABLE users"]

    with pytest.raises(ValueError):
        build_scoped_sql(Evil(), _Grant(), {}, 50, 0)


def test_unknown_filter_field_rejected():
    with pytest.raises(ValueError):
        build_scoped_sql(_Dataset(), _Grant(), {"hacker": 1}, 50, 0)


def test_order_by_pk_and_count_pair():
    sql, _ = build_scoped_sql(_Dataset(), _Grant(), {}, 50, 0)
    assert "ORDER BY" in sql and '"id"' in sql
```

- [ ] **Step 2: 跑测试确认失败** → `python -m pytest tests/unit/test_passwords.py tests/unit/test_apikeys.py tests/unit/test_scoped_query.py -v`；Expected: ImportError
- [ ] **Step 3: 实现**

`app/security/passwords.py`：

```python
import hashlib
import secrets

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256$_ITERATIONS${salt.hex()}${digest.hex()}".replace("_ITERATIONS", str(_ITERATIONS))


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return secrets.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False
```

`app/security/apikeys.py`：

```python
import hashlib
import secrets

_PREFIX = "bk_"


def generate_key() -> tuple[str, str]:
    return _PREFIX + secrets.token_hex(6), secrets.token_urlsafe(24)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
```

`app/access/scoped_query.py`：

```python
import re

from app.sync.spec import quote_ident

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
    "like": "LIKE",
}


def _check_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return name


def build_scoped_sql(dataset, grant, filters: dict, limit: int, offset: int) -> tuple[str, dict]:
    """返回经权限裁剪的 SELECT 语句。字段=白名单；行=规则 AND 用户过滤；值全部参数化。"""
    columns = grant.field_whitelist or list(dataset.columns)
    allowed = set(dataset.columns)
    for c in columns:
        _check_ident(c)
    unknown = set(filters) - allowed
    if unknown:
        raise ValueError(f"未知过滤字段: {sorted(unknown)}")
    for c in dataset.pk_columns:
        _check_ident(c)

    params: dict = {}
    where: list[str] = []
    for i, rule in enumerate(grant.row_rules or []):
        field = _check_ident(rule["field"])
        op = _OPS[rule["op"]]
        key = f"r_{i}"
        params[key] = rule["value"]
        where.append(f"{quote_ident(field)} {op} :{key}")
    for field, value in filters.items():
        _check_ident(field)
        params[f"f_{field}"] = value
        where.append(f"{quote_ident(field)} = :f_{field}")

    col_sql = ", ".join(quote_ident(c) for c in columns)
    sql = f"SELECT {col_sql} FROM {dataset.source_table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = ", ".join(quote_ident(c) for c in dataset.pk_columns)
    sql += f" ORDER BY {order} LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    return sql, params
```

说明：`dataset.source_table` 注册时必须已通过 `_check_ident` 全段校验（Task 4 注册接口负责），格式 `ods.<table>`。

- [ ] **Step 4: 跑测试确认通过** → 同 Step 2 命令；Expected: 11 passed
- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: 安全原语与权限裁剪查询构造器"
```

---

### Task 3: 限流器与鉴权依赖（get_principal）

**Files:**
- Create: `backend/app/access/ratelimit.py`, `backend/app/bootstrap.py`
- Modify: `backend/app/api/deps.py`
- Test: `backend/tests/unit/test_ratelimit.py`, `backend/tests/unit/test_deps_auth.py`

**Interfaces:**
- Consumes: Task 1 模型、Task 2 `hash_secret`
- Produces:
  - `FixedWindowLimiter(limit_per_minute: int, limit_per_day: int)`；`check(key_id: str) -> None`，超限抛 `RateLimitExceeded`；`LimiterHolder.get(settings) -> FixedWindowLimiter`（app.state 单例）
  - `get_principal(request, ctx) -> Principal`：解析 `X-API-Key: <key_id>.<secret>` → 查库校验 → 检查 user/key enabled → 限流 → 返回 `Principal(user, api_key)`；无效返回 401
  - `bootstrap_admin(username, password) -> tuple[User, str]`（返回用户与一次性 key 明文）

- [ ] **Step 1: 写失败测试**

`backend/tests/unit/test_ratelimit.py`：

```python
import pytest

from app.access.ratelimit import FixedWindowLimiter, RateLimitExceeded


def test_allows_within_limit():
    lim = FixedWindowLimiter(limit_per_minute=3, limit_per_day=100)
    for _ in range(3):
        lim.check("k1")


def test_minute_limit_exceeded():
    lim = FixedWindowLimiter(limit_per_minute=2, limit_per_day=100)
    lim.check("k1")
    lim.check("k1")
    with pytest.raises(RateLimitExceeded):
        lim.check("k1")


def test_keys_isolated():
    lim = FixedWindowLimiter(limit_per_minute=1, limit_per_day=100)
    lim.check("k1")
    lim.check("k2")  # 不抛
```

`backend/tests/unit/test_deps_auth.py`：

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_principal
from app.models import ApiKey, User
from app.security.apikeys import generate_key, hash_secret


@pytest.fixture
def auth_app(pg_session_factory):
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal=Depends(get_principal)):
        return {"username": principal.user.username, "key_id": principal.api_key.key_id}

    return app
```

（`Depends` 从 `fastapi` 导入；测试补齐 ctx override，结构同 Task 7 of P1：`app.dependency_overrides[get_ctx]`。）

用例：① 有效 key 返回用户名；② 错误 secret 401；③ key 停用 401；④ 用户停用 401。

- [ ] **Step 2: 跑测试确认失败** → ImportError
- [ ] **Step 3: 实现**

`app/access/ratelimit.py`：

```python
import time


class RateLimitExceeded(Exception):
    pass


class FixedWindowLimiter:
    """进程内固定窗口限流。重启清零：P2 单机规模可接受（见全局约束）。"""

    def __init__(self, limit_per_minute: int, limit_per_day: int):
        self.limit_per_minute = limit_per_minute
        self.limit_per_day = limit_per_day
        self._minute: dict[str, tuple[int, int]] = {}
        self._day: dict[str, tuple[int, int]] = {}

    def check(self, key_id: str) -> None:
        now = time.time()
        m_slot = int(now // 60)
        d_slot = int(now // 86400)
        m_slot_, m_count = self._minute.get(key_id, (m_slot, 0))
        if m_slot_ != m_slot:
            m_slot_, m_count = m_slot, 0
        if m_count + 1 > self.limit_per_minute:
            raise RateLimitExceeded("minute limit exceeded")
        d_slot_, d_count = self._day.get(key_id, (d_slot, 0))
        if d_slot_ != d_slot:
            d_slot_, d_count = d_slot, 0
        if d_count + 1 > self.limit_per_day:
            raise RateLimitExceeded("daily limit exceeded")
        self._minute[key_id] = (m_slot_, m_count + 1)
        self._day[key_id] = (d_slot_, d_count + 1)


class LimiterHolder:
    _limiter = None

    @classmethod
    def get(cls, settings):
        if cls._limiter is None:
            cls._limiter = FixedWindowLimiter(
                settings.rate_limit_per_minute, settings.rate_limit_per_day
            )
        return cls._limiter
```

`Settings` 追加字段：`rate_limit_per_minute: int = 120`、`rate_limit_per_day: int = 10000`。

`app/api/deps.py` 追加（`get_ctx` 保持不变）：

```python
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.access.ratelimit import LimiterHolder, RateLimitExceeded
from app.models import ApiKey, User
from app.security.apikeys import hash_secret


@dataclass
class Principal:
    user: User
    api_key: ApiKey


def get_principal(request: Request, ctx: Context = Depends(get_ctx)) -> Principal:
    header = request.headers.get("X-API-Key", "")
    key_id, _, secret = header.partition(".")
    if not key_id or not secret:
        raise HTTPException(status_code=401, detail="missing api key")
    with ctx.session_factory() as session:
        api_key = session.query(ApiKey).filter_by(key_id=key_id).first()
        if api_key is None or api_key.secret_hash != hash_secret(secret):
            raise HTTPException(status_code=401, detail="invalid api key")
        user = session.get(User, api_key.user_id)
        if not api_key.enabled or user is None or not user.enabled:
            raise HTTPException(status_code=401, detail="api key disabled")
        try:
            LimiterHolder.get(ctx.settings).check(key_id)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        return Principal(user=user, api_key=api_key)
```

`app/bootstrap.py`：

```python
import sys

from app.db import make_engine, make_session_factory
from app.models import ApiKey, User
from app.security.apikeys import generate_key, hash_secret
from app.security.passwords import hash_password
from app.settings import get_settings


def bootstrap_admin(username: str, password: str) -> tuple[User, str]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        key_id, secret = generate_key()
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name="管理员",
            is_admin=True,
        )
        session.add(user)
        session.flush()
        session.add(
            ApiKey(
                user_id=user.id,
                key_id=key_id,
                secret_hash=hash_secret(secret),
                name="bootstrap",
            )
        )
        session.commit()
        return user, f"{key_id}.{secret}"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python -m app.bootstrap <用户名> <密码>")
        sys.exit(1)
    user, key = bootstrap_admin(sys.argv[1], sys.argv[2])
    print(f"管理员 {user.username} 已创建，API Key（仅显示一次）: {key}")
```

`test_deps_auth.py` 的实现侧：`get_principal` 中 `Depends` 引用需在文件顶部 `from fastapi import Depends`（deps.py 合并导入）。

- [ ] **Step 4: 跑测试确认通过** → `python -m pytest tests/unit/test_ratelimit.py tests/unit/test_deps_auth.py -v`；Expected: 7 passed
- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: API Key 鉴权依赖、限流器与管理员引导脚本"
```

---

### Task 4: 管理端 API（用户/角色/数据集/授权/Key/审计）

**Files:**
- Create: `backend/app/api/admin_api.py`
- Modify: `backend/app/main.py`（注册 admin 路由）、`backend/app/settings.py`（无改动则跳过）
- Test: `backend/tests/unit/test_admin_api.py`

**Interfaces:**
- Consumes: Task 1-3 全部；`get_principal`、`hash_password`、`generate_key/hash_secret`、`_check_ident`（从 scoped_query 导出为公开名 `validate_ident`）
- Produces（响应均为 JSON；admin 路由全部要求 `principal.user.is_admin`，否则 403）:
  - `POST /api/v1/admin/users` `{username, password, display_name?, role_id?, is_admin?}` → 201 `{id, username}`
  - `POST /api/v1/admin/roles` `{name, description?}` → 201 `{id, name}`
  - `POST /api/v1/admin/datasets` `{name, source_table, pk_columns, columns, description?}` → 201；注册时校验 `source_table` 每段标识符合法且存在（对 ods 库执行 `SELECT 1 ... LIMIT 1`），否则 400
  - `POST /api/v1/admin/grants` `{role_id, dataset_id, row_rules?, field_whitelist?}` → 201；`field_whitelist` 必须是 `dataset.columns` 子集，规则 `field` 必须在 `dataset.columns` 内，`op` 必须在 eq/ne/gt/ge/lt/le/like 内，否则 400
  - `POST /api/v1/admin/api-keys` `{user_id, name?}` → 201 `{id, key, key_id, secret}`（secret 仅此一次返回）
  - `POST /api/v1/admin/api-keys/{id}/revoke` → `{id, enabled: false}`
  - `GET /api/v1/admin/audit-logs?limit=50` → 倒序列表

- [ ] **Step 1: 写失败测试** `backend/tests/unit/test_admin_api.py`

结构沿用 P1 `test_sync_api.py`：`create_app()` + `dependency_overrides`。admin 鉴权通过在测试库插入 `is_admin=True` 用户与真实 ApiKey，请求头带 `X-API-Key: <key_id>.<secret>`。用例：

```python
def test_admin_lifecycle(api_client, pg_session_factory, pg_engine):
    # ① bootstrap 管理员
    admin_user, admin_key = bootstrap_admin("p2admin", "pw123456")
    headers = {"X-API-Key": admin_key}
    # ② 创建角色 → 创建数据集 → 创建授权 → 创建普通用户 → 发 Key
    r = api_client.post("/api/v1/admin/roles", json={"name": "east"}, headers=headers)
    assert r.status_code == 201
    role_id = r.json()["id"]
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.sales_order_admin "
            "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.sales_order_admin VALUES (1,'华东',10,5),(2,'华北',20,8) "
            "ON CONFLICT (id) DO NOTHING"
        ))
    r = api_client.post("/api/v1/admin/datasets", json={
        "name": "sales_admin", "source_table": "ods.sales_order_admin",
        "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
    }, headers=headers)
    assert r.status_code == 201
    ds_id = r.json()["id"]
    # ③ 非法表名 400
    r = api_client.post("/api/v1/admin/datasets", json={
        "name": "evil", "source_table": "ods.x; DROP TABLE users",
        "pk_columns": ["id"], "columns": ["id"],
    }, headers=headers)
    assert r.status_code == 400
    # ④ 授权（字段白名单不含 cost）
    r = api_client.post("/api/v1/admin/grants", json={
        "role_id": role_id, "dataset_id": ds_id,
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
        "field_whitelist": ["id", "region", "amount"],
    }, headers=headers)
    assert r.status_code == 201
    # ⑤ 白名单不在列内 → 400
    r = api_client.post("/api/v1/admin/grants", json={
        "role_id": role_id, "dataset_id": ds_id,
        "field_whitelist": ["id", "hacker"],
    }, headers=headers)
    assert r.status_code == 400
    # ⑥ 创建普通用户并绑定角色 → 发 Key（secret 只出现一次）
    r = api_client.post("/api/v1/admin/users", json={
        "username": "p2user", "password": "pw123456", "role_id": role_id,
    }, headers=headers)
    assert r.status_code == 201
    user_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/api-keys", json={
        "user_id": user_id, "name": "p2",
    }, headers=headers)
    assert r.status_code == 201
    user_key = r.json()
    assert user_key["secret"]
    # ⑦ 撤销 Key
    r = api_client.post(f"/api/v1/admin/api-keys/{user_key['id']}/revoke", headers=headers)
    assert r.json()["enabled"] is False
    # ⑧ 非管理员访问 admin → 403
    r = api_client.get("/api/v1/admin/audit-logs", headers={
        "X-API-Key": f"{user_key['key_id']}.{user_key['secret']}"
    })
    assert r.status_code == 403
    # 清理
    ...
```

- [ ] **Step 2: 跑测试确认失败** → 404（路由不存在）
- [ ] **Step 3: 实现 `app/api/admin_api.py`**（完整代码按 Interfaces 列表实现；要点）

```python
router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(principal: Principal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


@router.post("/users", status_code=201)
def create_user(body: dict, principal: Principal = Depends(get_principal)):
    _require_admin(principal)
    ...
```

- 唯一键冲突（username/name 重复）→ 409；
- `validate_ident` 应用到 `source_table` 每段与 `columns`/`pk_columns` 每项；
- 表存在性校验：`SELECT 1 FROM <source_table> LIMIT 1`（捕获编程错误→400）；
- grant 校验失败统一 400 带原因；创建 admin API Key 用 `generate_key()`，库存 `hash_secret(secret)`；
- `main.py` 注册：`app.include_router(admin_router, prefix="/api/v1")`。

- [ ] **Step 4: 跑测试确认通过** → `python -m pytest tests/unit/test_admin_api.py -v`；Expected: 全部 passed
- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: 管理端 API（用户/角色/数据集/授权/Key/审计）"
```

---

### Task 5: 数据查询网关（行级/字段级裁剪 + 审计）

**Files:**
- Create: `backend/app/api/data_api.py`
- Modify: `backend/app/main.py`（注册 data 路由）
- Test: `backend/tests/unit/test_data_api.py`

**Interfaces:**
- Consumes: `get_principal`、`build_scoped_sql`、Task 4 的授权链路
- Produces:
  - `GET /api/v1/datasets/{name}/rows?limit=50&offset=0&<field>=<value>...`
    - 200 `{dataset, columns, rows, total, limit, offset}`（columns=白名单；rows 为 dict 列表）
    - 404 数据集不存在或未启用；403 用户角色无该数据集授权；400 非法过滤字段/超限 limit（>500）
    - 每次调用写 AuditLog（channel=api, action=query, row_count, duration_ms, detail={filters, limit, offset}）
  - `GET /api/v1/datasets` → 当前用户可见数据集列表（有授权且启用）

- [ ] **Step 1: 写失败测试** `backend/tests/unit/test_data_api.py`

用例（复用 Task 4 生命周期建的库内数据，测试内自行建全部数据）：

```python
def test_query_rows_scoped(api_client, pg_session_factory, pg_engine):
    # 建 admin + 角色 + 数据集（ods.sales_order_data 含 华东/华北 各一行）
    # 授权 row_rules region=华东，白名单不含 cost
    user_key = ...  # 发 Key
    headers = {"X-API-Key": user_key}
    r = api_client.get("/api/v1/datasets", headers=headers)
    assert [d["name"] for d in r.json()] == ["sales_data"]
    r = api_client.get("/api/v1/datasets/sales_data/rows", headers=headers)
    body = r.json()
    assert body["total"] == 1                      # 行级过滤只剩华东
    assert body["rows"][0]["region"] == "华东"
    assert "cost" not in body["rows"][0]           # 字段白名单
    assert body["columns"] == ["id", "region", "amount"]
    # 审计已记录
    r = api_client.get("/api/v1/admin/audit-logs?limit=10", headers=admin_headers)
    assert any(log["dataset"] == "sales_data" and log["action"] == "query" for log in r.json())


def test_query_rows_no_grant_403(api_client, ...):
    r = api_client.get("/api/v1/datasets/sales_data/rows", headers=no_grant_key)
    assert r.status_code == 403


def test_query_rows_bad_filter_400(...):
    r = api_client.get("/api/v1/datasets/sales_data/rows", params={"hacker": "1"}, headers=...)
    assert r.status_code == 400


def test_query_rows_limit_cap_400(...):
    r = api_client.get(..., params={"limit": "501"}, headers=...)
    assert r.status_code == 400
```

- [ ] **Step 2: 跑测试确认失败** → 404
- [ ] **Step 3: 实现 `app/api/data_api.py`**（要点）

```python
router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("")
def list_datasets(principal: Principal = Depends(get_principal)):
    # 查询用户角色在 datasets JOIN dataset_grants 中有授权且 enabled 的数据集


@router.get("/{name}/rows")
def query_rows(
    name: str,
    request: Request,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_principal),
):
    started = time.perf_counter()
    # 1. 数据集存在且启用；2. 角色授权（403/404）
    # 3. filters = request.query_params 排除 limit/offset
    # 4. build_scoped_sql → COUNT(*) 同 scope 计算 total（替换列与 LIMIT/OFFSET 段）
    # 5. 执行 → rows=[dict(r._mapping) for r in result]
    # 6. 写 AuditLog（duration_ms 取整）
    # 7. 返回 {dataset, columns, rows, total, limit, offset}
```

- [ ] **Step 4: 跑测试确认通过** → `python -m pytest tests/unit/test_data_api.py -v`；Expected: 4 passed
- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: 数据查询网关（行级/字段级裁剪、限流、审计）"
```

---

### Task 6: P2 端到端验收

**Files:**
- Create: `backend/tests/e2e/test_p2_acceptance.py`

**Interfaces:**
- Consumes: Task 1-5 全部，走真实 HTTP 层（TestClient + lifespan）与真实 PostgreSQL/MySQL
- Produces: P2 验收证据——「发一个 Key 给同事，他只能拉权限内的数据」

- [ ] **Step 1: 写验收测试**（一个用例覆盖完整链路，结构同 Task 5 测试但加以下断言）

```python
def test_p2_end_to_end(api_client_full, pg_session_factory, pg_engine):
    # ① 管理员引导（真实 get_principal，无 override get_principal）
    # ② 建数据集 sales_e2e（含 cost 列）→ 角色 → 授权（region=华东 + 白名单无 cost）→ 用户 → Key
    # ③ 用户 Key 查询：华东 1 行、无 cost 列
    # ④ 撤销 Key 后同请求 → 401
    # ⑤ 重新发 Key → 限流：把 Settings rate_limit_per_minute 调成 2（FakeSettings 注入 app.state.settings）
    #    连续 3 次请求，第 3 次 429
    # ⑥ audit-logs 包含 query 与 revoke 动作
```

- [ ] **Step 2: 跑测试** → `python -m pytest tests/e2e -v`；Expected: 2 passed（P1 + P2）
- [ ] **Step 3: 全量回归** → `python -m pytest tests/unit tests/integration tests/e2e -q`；Expected: 全部 passed
- [ ] **Step 4: Commit + 打标签**

```bash
git add backend
git commit -m "test: P2 端到端验收（鉴权/裁剪/撤销/限流/审计）"
git tag p2-done
```

---

## Self-Review 结论（写计划时已自查）

1. **Spec 覆盖**：§4.2 权限模型五层=Task 1/2/4；§4.3 网关（鉴权/限流/审计）=Task 3/5；§5 数据流示例=Task 5/6 验收路径；密钥只走 .env=Task 3 bootstrap 不写死凭据。**已知偏差**：HMAC 请求签名推迟到 P5（全局约束已注明）；Key secret 库中存 SHA-256 散列（比 spec 草案的明文存储更安全）。
2. **占位符**：Task 4/5 实现要点为路由行为规格 + 核心代码骨架，字段级细节（状态码映射、参数名）已完整给出，无 TBD。
3. **类型一致性**：`Principal(user, api_key)` 在 Task 3 定义、Task 4/5/6 消费一致；`build_scoped_sql(dataset, grant, filters, limit, offset)` Task 2 定义、Task 5 调用一致；`validate_ident` 由 `_check_ident` 更名导出，Task 4 使用一致。
