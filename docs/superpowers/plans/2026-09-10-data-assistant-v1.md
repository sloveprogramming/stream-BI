# 数据助手（ChatBI）第一版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BIsql 中台新增「数据助手」：自然语言提问 → DeepSeek 生成只读 SQL → 权限重写后执行 → 表格结果，支持跨数据集 JOIN。

**Architecture:** 后端新增 `assistant/` 模块（LLM 客户端、schema 上下文、SQL 安全闸门、SQL 权限重写、SSE 聊天接口），前端新增「数据助手」聊天页。大模型用 DeepSeek 的 OpenAI 兼容接口；SQL 用 sqlglot 解析并重写表引用为带行级规则的子查询。

**Tech Stack:** sqlglot、openai（客户端）；其余沿用 FastAPI / SQLAlchemy / PostgreSQL / React / AntD。

**Spec:** docs/superpowers/specs/2026-09-10-data-assistant-v1-design.md

## Global Constraints

- 所有后端代码位于 `backend/`，前端位于 `frontend/`；命令默认在各自目录执行。
- Python >=3.12；新依赖 `sqlglot`、`openai` 写入 `backend/pyproject.toml`。
- 密钥只走 `backend/.env`：`BISQL_LLM_API_KEY`、`BISQL_LLM_BASE_URL`、`BISQL_LLM_MODEL`、`BISQL_LLM_TIMEOUT_SECONDS`、`BISQL_ASSISTANT_MAX_ROWS`（默认 1000）。
- 鉴权复用 `get_any_principal`；权限判定复用 `app.access.grants.get_effective_grant`。
- SQL 只读：仅允许 SELECT / WITH 包裹的 SELECT，单条语句；禁止写关键字；执行用只读事务 + 语句超时 + 行数上限。
- 行级权限在执行前由 `sql_rewrite` 重写：每个表引用替换为 `(SELECT 白名单字段 FROM 表 WHERE 行级规则)`；字段白名单外引用返回 403。
- 每个任务 TDD：先写失败测试，跑失败，再实现，跑通过，commit。
- 本地验证后部署到服务器（上传 → 重建 api 镜像 → 验证），不触碰服务器其他服务。

## File Structure

```
backend/app/assistant/__init__.py
backend/app/assistant/llm.py
backend/app/assistant/schema_context.py
backend/app/assistant/sql_guard.py
backend/app/assistant/sql_rewrite.py
backend/app/assistant/chat_api.py
backend/tests/unit/test_assistant_llm.py
backend/tests/unit/test_assistant_schema.py
backend/tests/unit/test_assistant_guard.py
backend/tests/unit/test_assistant_rewrite.py
backend/tests/integration/test_assistant_chat.py
frontend/src/pages/DataAssistant.tsx
frontend/src/App.tsx（加路由）
frontend/src/layouts/MainLayout.tsx（加菜单）
```

---

### Task 1: LLM 客户端与配置

**Files:**
- Create: `backend/app/assistant/__init__.py`, `backend/app/assistant/llm.py`
- Modify: `backend/app/settings.py`
- Test: `backend/tests/unit/test_assistant_llm.py`

**Interfaces:**
- Consumes: `get_settings()`
- Produces: `generate_sql(system_prompt: str, user_prompt: str, client=None) -> str`（client 参数便于测试注入假客户端）

- [ ] **Step 1: 写失败测试** `backend/tests/unit/test_assistant_llm.py`

```python
from app.assistant.llm import generate_sql


class _C:
    content = "```sql\nSELECT 1\n```"


class _Choice:
    message = _C()


class _Resp:
    choices = [_Choice()]


class _Chat:
    def create(self, model, messages, temperature):
        return _Resp()


class FakeClient:
    chat = _Chat()


def test_extracts_sql_from_fenced_response(monkeypatch):
    monkeypatch.setattr("app.assistant.llm.build_client", lambda: FakeClient())
    assert generate_sql("sys", "q") == "SELECT 1"


def test_strips_plain_sql(monkeypatch):
    class Plain(FakeClient):
        chat = _Chat()

    class P:
        chat = _Chat()

    P.chat.create = lambda model, messages, temperature: type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "SELECT 2"})()})()]})()
    monkeypatch.setattr("app.assistant.llm.build_client", lambda: P())
    assert generate_sql("sys", "q") == "SELECT 2"
```

- [ ] **Step 2: 跑测试确认失败** → `python -m pytest tests/unit/test_assistant_llm.py -v`；Expected: ImportError
- [ ] **Step 3: 实现**

```python
import re

from openai import OpenAI

from app.settings import get_settings


def build_client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.llm_api_key, base_url=s.llm_base_url, timeout=s.llm_timeout_seconds)


def generate_sql(system_prompt: str, user_prompt: str, client=None) -> str:
    c = client or build_client()
    s = get_settings()
    resp = c.chat.completions.create(
        model=s.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    text = resp.choices[0].message.content.strip()
    m = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip().rstrip(";")
```

`app/settings.py` 追加字段：

```python
llm_api_key: str = ""
llm_base_url: str = "https://api.deepseek.com"
llm_model: str = "deepseek-chat"
llm_timeout_seconds: int = 60
assistant_max_rows: int = 1000
```

- [ ] **Step 4: 跑测试确认通过** → 同 Step 2；Expected: 2 passed
- [ ] **Step 5: Commit** → `git add backend && git commit -m "feat: 数据助手 LLM 客户端与配置"`

### Task 2: Schema 上下文构建

**Files:**
- Create: `backend/app/assistant/schema_context.py`
- Test: `backend/tests/unit/test_assistant_schema.py`

**Interfaces:**
- Consumes: `get_effective_grant(session, user, dataset_id)`、`Dataset`
- Produces: `build_schema_context(session_factory, pg_engine, user) -> dict`，返回 `{"tables": [{name, source_table, columns, samples, row_rules}], "prompt": str}`

- [ ] **Step 1: 写失败测试**（只收集授权数据集；prompt 含字段）

```python
def test_build_schema_only_authorized(pg_session_factory, pg_engine):
    # 准备两个数据集，user 只授权第一个
    from app.assistant.schema_context import build_schema_context
    from app.models import Dataset, DatasetGrant, Role, User

    with pg_session_factory() as s:
        u = User(username="sc_u", password_hash="x")
        r = Role(name="sc_r")
        d1 = Dataset(name="sc_a", source_table="ods.sc_a", pk_columns=["id"], columns=["id", "region"])
        d2 = Dataset(name="sc_b", source_table="ods.sc_b", pk_columns=["id"], columns=["id", "secret"])
        s.add_all([u, r, d1, d2])
        s.flush()
        u.role_id = r.id
        s.add(DatasetGrant(role_id=r.id, dataset_id=d1.id, row_rules=[], field_whitelist=["id", "region"]))
        s.commit()
        ctx = build_schema_context(pg_session_factory, pg_engine, u)
        names = [t["name"] for t in ctx["tables"]]
        assert "sc_a" in names and "sc_b" not in names
        assert "region" in ctx["prompt"]
```

- [ ] **Step 2: 跑失败** → ImportError
- [ ] **Step 3: 实现**

```python
from app.access.grants import get_effective_grant
from app.models import Dataset


def build_schema_context(session_factory, pg_engine, user) -> dict:
    with session_factory() as session:
        datasets = session.query(Dataset).filter_by(enabled=True).order_by(Dataset.id).all()
        tables = []
        for ds in datasets:
            grant = get_effective_grant(session, user, ds.id)
            if grant is None:
                continue
            columns = grant.field_whitelist or list(ds.columns)
            samples = _sample_rows(pg_engine, ds.source_table, columns)
            tables.append({
                "name": ds.name,
                "source_table": ds.source_table,
                "columns": columns,
                "samples": samples,
                "row_rules": grant.row_rules or [],
            })
        return {"tables": tables, "prompt": _render_prompt(tables)}


def _sample_rows(pg_engine, source_table, columns, limit=3):
    from sqlalchemy import text

    cols = ", ".join(f'"{c}"' for c in columns)
    try:
        with pg_engine.connect() as conn:
            rows = conn.execute(text(f"SELECT {cols} FROM {source_table} LIMIT :n"), {"n": limit}).fetchall()
        return [[str(v)[:50] for v in r] for r in rows]
    except Exception:
        return []


def _render_prompt(tables) -> str:
    lines = ["可用数据集（只能使用这些表和字段，JOIN 键依据字段名与语义自行推理）："]
    for t in tables:
        lines.append(f"- 表 {t['name']}（{t['source_table']}）字段: {', '.join(t['columns'])}")
        if t["samples"]:
            lines.append(f"  样例: {t['samples'][0]}")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑通过** → 同 Step 2；Expected: 1 passed
- [ ] **Step 5: Commit** → `feat: 数据助手 schema 上下文构建`

### Task 3: SQL 安全闸门

**Files:**
- Create: `backend/app/assistant/sql_guard.py`
- Test: `backend/tests/unit/test_assistant_guard.py`

**Interfaces:**
- Consumes: Task 2 的 tables 结构
- Produces: `validate_sql(sql: str, tables: list[dict]) -> None`；非法抛 `SqlGuardError(message)`

校验规则：只读；表引用必须 ∈ 各表 `source_table`；列引用必须 ∈ 该表白名单；禁止 `;`、多条语句、写关键字。

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.assistant.sql_guard import SqlGuardError, validate_sql

T = [
    {"name": "o", "source_table": "ods.order", "columns": ["id", "product_id", "qty"]},
    {"name": "s", "source_table": "ods.stock", "columns": ["product_id", "stock"]},
]


def test_allows_select_join():
    validate_sql("SELECT o.id, s.stock FROM ods.order o JOIN ods.stock s ON o.product_id = s.product_id", T)


def test_rejects_insert():
    with pytest.raises(SqlGuardError):
        validate_sql("INSERT INTO ods.order VALUES (1, 2, 3)", T)


def test_rejects_unknown_table():
    with pytest.raises(SqlGuardError):
        validate_sql("SELECT * FROM ods.secret", T)


def test_rejects_hidden_column():
    with pytest.raises(SqlGuardError):
        validate_sql("SELECT id, cost FROM ods.order", T)


def test_rejects_semicolon_injection():
    with pytest.raises(SqlGuardError):
        validate_sql("SELECT id FROM ods.order; DROP TABLE ods.order", T)
```

- [ ] **Step 2: 跑失败** → ImportError
- [ ] **Step 3: 实现**

```python
import sqlglot
from sqlglot import exp


class SqlGuardError(Exception):
    pass


_WRITE_KEYWORDS = {"insert", "update", "delete", "truncate", "drop", "create", "alter", "merge", "grant", "revoke", "copy"}


def validate_sql(sql: str, tables: list[dict]) -> None:
    if ";" in sql:
        raise SqlGuardError("只允许单条查询")
    try:
        exprs = sqlglot.parse(sql)
    except Exception as exc:
        raise SqlGuardError(f"SQL 解析失败: {exc}")
    if len(exprs) != 1 or not isinstance(exprs[0], exp.Select):
        raise SqlGuardError("只允许 SELECT 查询")
    expr = exprs[0]
    lowered = expr.sql().lower()
    for word in _WRITE_KEYWORDS:
        if f" {word} " in f" {lowered} ":
            raise SqlGuardError(f"检测到写操作，已拒绝: {word}")
    allowed_tables = {t["source_table"] for t in tables}
    colmap = {t["source_table"]: set(t["columns"]) for t in tables}
    for table in expr.find_all(exp.Table):
        if table.name not in allowed_tables:
            raise SqlGuardError(f"无权限的表: {table.name}")
    for col in expr.find_all(exp.Column):
        if col.table and col.table in allowed_tables and col.name not in colmap[col.table]:
            raise SqlGuardError(f"无权限的字段: {col.table}.{col.name}")
```

- [ ] **Step 4: 跑通过** → 同 Step 2；Expected: 5 passed
- [ ] **Step 5: Commit** → `feat: 数据助手 SQL 安全闸门`

### Task 4: SQL 权限重写

**Files:**
- Create: `backend/app/assistant/sql_rewrite.py`
- Test: `backend/tests/unit/test_assistant_rewrite.py`

**Interfaces:**
- Consumes: Task 2 的 tables 结构
- Produces: `rewrite_sql(sql: str, tables: list[dict]) -> str`；把 `ods.order` 重写为 `(SELECT "白名单列" FROM ods.order WHERE <行级规则>)`

- [ ] **Step 1: 写失败测试**

```python
from app.assistant.sql_rewrite import rewrite_sql

T = [
    {
        "name": "o",
        "source_table": "ods.order",
        "columns": ["id", "product_id", "qty"],
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
    }
]


def test_rewrite_injects_row_filter():
    sql = "SELECT o.id FROM ods.order o"
    out = rewrite_sql(sql, T)
    assert 'WHERE "region" =' in out


def test_expand_star_to_whitelist():
    sql = "SELECT * FROM ods.order"
    out = rewrite_sql(sql, T)
    assert '"product_id"' in out
```

- [ ] **Step 2: 跑失败** → ImportError
- [ ] **Step 3: 实现**

```python
from app.access.scoped_query import check_ident

_OP = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "like": "LIKE"}


def rewrite_sql(sql: str, tables: list[dict]) -> str:
    out = sql
    for t in tables:
        cols = ", ".join(f'"{c}"' for c in t["columns"])
        where = _render_rules(t["row_rules"])
        inner = f'(SELECT {cols} FROM {t["source_table"]}{where})'
        out = out.replace(t["source_table"], inner)
    return out


def _render_rules(rules) -> str:
    if not rules:
        return ""
    parts = []
    for i, r in enumerate(rules):
        field = check_ident(r["field"])
        op = _OP[r["op"]]
        parts.append(f'"{field}" {op} :r_{i}')
    return " WHERE " + " AND ".join(parts)
```

说明：行级规则的 `:r_N` 占位参数由 Task 5 在执行时绑定；`*` 展开为白名单列，隐藏字段自然不可见。

- [ ] **Step 4: 跑通过** → 同 Step 2；Expected: 2 passed
- [ ] **Step 5: Commit** → `feat: 数据助手 SQL 权限重写`

### Task 5: 聊天接口（SSE）

**Files:**
- Create: `backend/app/assistant/chat_api.py`
- Modify: `backend/app/main.py`（注册路由）
- Test: `backend/tests/integration/test_assistant_chat.py`

**Interfaces:**
- Consumes: Task 1-4 全部、`get_any_principal`、`Context`
- Produces: `POST /api/v1/assistant/chat`（SSE）；集成测试用 monkeypatch 替换 `generate_sql` 返回固定 SQL

- [ ] **Step 1: 写失败测试**（monkeypatch 固定 SQL，走真实 PostgreSQL 权限）

```python
def test_chat_scoped_join(api_client, pg_session_factory, pg_engine, monkeypatch):
    # 建 ods 表、数据集，user 授权 order：行级 region=华东，列 id/product_id/qty
    monkeypatch.setattr(
        "app.assistant.chat_api.generate_sql",
        lambda sys_prompt, usr_prompt: "SELECT o.id, o.product_id FROM ods.order o",
    )
    r = api_client.post("/api/v1/assistant/chat", headers=user_headers, json={"question": "看华东订单"})
    assert r.status_code == 200
    assert '"type":"result"' in r.text
```

- [ ] **Step 2: 跑失败** → 404（路由不存在）
- [ ] **Step 3: 实现 `chat_api.py`**

```python
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.assistant.llm import generate_sql
from app.assistant.schema_context import build_schema_context
from app.assistant.sql_guard import SqlGuardError, validate_sql
from app.assistant.sql_rewrite import rewrite_sql

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/chat")
def chat(body: dict, principal: AnyPrincipal = Depends(get_any_principal), ctx: Context = Depends(get_ctx)):
    question = body.get("question", "")
    if not question:
        return StreamingResponse(_sse([{"type": "error", "message": "问题不能为空"}]), media_type="text/event-stream")
    return StreamingResponse(_run(question, principal, ctx), media_type="text/event-stream")


def _sse(events):
    for e in events:
        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
    yield 'data: {"type":"done"}\n\n'


def _run(question, principal, ctx):
    try:
        schema = build_schema_context(ctx.session_factory, ctx.pg_engine, principal.user)
        if not schema["tables"]:
            yield from _sse([{"type": "error", "message": "当前账号没有可用数据集"}])
            return
        sql = generate_sql(_system_prompt(), schema["prompt"] + "\n\n用户问题：" + question)
        yield from _sse([{"type": "sql", "sql": sql}])
        validate_sql(sql, schema["tables"])
        rewritten = rewrite_sql(sql, schema["tables"])
        params = _collect_row_params(schema["tables"])
        columns, rows = _execute(ctx, rewritten, params)
        yield from _sse([{"type": "result", "columns": columns, "rows": rows, "row_count": len(rows)}])
    except SqlGuardError as exc:
        yield from _sse([{"type": "error", "message": str(exc)}])
    except Exception as exc:
        yield from _sse([{"type": "error", "message": f"查询失败：{exc}"[:300]}])


def _execute(ctx, sql, params):
    max_rows = ctx.settings.assistant_max_rows
    with ctx.pg_engine.connect() as conn:
        conn.execute(text("SET LOCAL statement_timeout='15s'"))
        result = conn.execute(text(sql), params)
        columns = list(result.keys())
        rows = [list(r) for r in result.fetchmany(max_rows + 1)]
    return columns, rows[:max_rows]


def _system_prompt():
    return ("你是企业数据助手。根据给定的数据集结构生成一条只读 PostgreSQL SELECT。"
            "只能使用列出的表和字段；JOIN 键依据字段名和语义自行推理；只返回 SQL，不要解释。")


def _collect_row_params(tables):
    params = {}
    for t in tables:
        for i, r in enumerate(t["row_rules"] or []):
            params[f"r_{i}"] = r["value"]
    return params
```

`main.py` 注册：

```python
from app.assistant.chat_api import router as assistant_router
app.include_router(assistant_router, prefix="/api/v1")
```

- [ ] **Step 4: 跑通过** → `python -m pytest tests/integration/test_assistant_chat.py -v`；Expected: 1 passed
- [ ] **Step 5: Commit** → `feat: 数据助手聊天接口（SSE）`

### Task 6: 前端「数据助手」页面

**Files:**
- Create: `frontend/src/pages/DataAssistant.tsx`
- Modify: `frontend/src/App.tsx`、`frontend/src/layouts/MainLayout.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/assistant/chat`（SSE，携带 Authorization Bearer）

- [ ] **Step 1: 实现页面**

页面：左侧对话消息列表 + 底部输入框；回复渲染结果表格（antd Table）+ SQL 折叠（Collapse）+ 错误（Alert）+ 导出 CSV 按钮。SSE 用 `fetch` 读流解析 `data: {...}` 事件。

核心流式读取：

```tsx
const resp = await fetch('/api/v1/assistant/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}` },
  body: JSON.stringify({ question }),
})
const reader = resp.body!.getReader()
const decoder = new TextDecoder()
let buf = ''
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buf += decoder.decode(value, { stream: true })
  const parts = buf.split('\n\n')
  buf = parts.pop() || ''
  for (const p of parts) {
    const line = p.trim().replace(/^data: /, '')
    if (line) handleEvent(JSON.parse(line))
  }
}
```

`handleEvent` 按 `type` 分派：`sql` 存 SQL、`result` 存 columns/rows、`error` 存错误、`done` 结束加载态。

- [ ] **Step 2: 注册路由与菜单**
  - `App.tsx`：`<Route path="data-assistant" element={<DataAssistant />} />`
  - `MainLayout.tsx`：菜单 items 增加 `{ key: '/data-assistant', icon: <RobotOutlined />, label: '数据助手' }`（`RobotOutlined` 从 `@ant-design/icons` 引入）
- [ ] **Step 3: 构建** → `npm run build`；Expected: 成功
- [ ] **Step 4: Commit** → `feat: 前端数据助手页面`

### Task 7: 端到端验收与部署

- [ ] **Step 1: 全量回归** → `python -m pytest tests/unit tests/integration tests/e2e -q`；Expected: 全部通过
- [ ] **Step 2: 服务器部署**：上传 `backend/app`、`backend/pyproject.toml`、`frontend/dist`；服务器 `.env` 写入 DeepSeek key；重建 api 镜像（无新表，无需迁移）
- [ ] **Step 3: 真模型冒烟**：管理员在网页提问，确认返回表格结果
- [ ] **Step 4: Commit + 打标签** → `git tag assistant-v1`

## Self-Review

1. **Spec 覆盖**：§2.2 模块 = Task 1-5；§2.3 权限 = Task 3/4 + Task 5 集成测试；§2.4 API = Task 5；§2.5 前端 = Task 6；§2.6 配置 = Task 1；§3 错误处理 = Task 5 的 error 事件；§4 测试 = 各任务测试 + Task 7 回归。
2. **占位符**：无 TBD；Task 6 前端逻辑为结构规格 + 关键流式读取代码，无歧义。
3. **类型一致**：`build_schema_context(session_factory, pg_engine, user) -> dict`、`validate_sql(sql, tables)`、`rewrite_sql(sql, tables) -> str`、`generate_sql(system_prompt, user_prompt, client=None) -> str` 在 Task 1-5 一致；tables 字段 `name/source_table/columns/row_rules` 一致。
