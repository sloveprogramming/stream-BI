"""Agent 与问数链路共用的取数工具（进程内，不经过 HTTP/MCP）。

关键约束：**取数只有这一条路** ——
    load_schema()  取「该用户有权限的数据集结构」
    run_scoped_sql() 先过安全闸门，再按权限重写，最后只读执行
于是 Web / REST / MCP / 数据助手 / Agent 五条出口共用同一套裁剪口径，
不会出现「某个通道忘了加行级规则」这种事。
"""
from sqlalchemy import text

from app.access.scoped_query import build_scoped_sql
from app.assistant.schema_context import build_schema_context
from app.assistant.sql_guard import SqlGuardError, validate_sql
from app.assistant.sql_rewrite import rewrite_sql

DEFAULT_TIMEOUT_SECONDS = 15


def load_schema(session_factory, pg_engine, user, tenant_id: int | None = None) -> dict:
    """返回 `{tables: [...], prompt: str}`，只含该用户可见的数据集。

    `tenant_id` 由调用方（请求的租户上下文）传入；问数与 Agent 都只看到
    自己租户内的数据集结构。
    """
    return build_schema_context(session_factory, pg_engine, user, tenant_id)


def load_few_shots(session_factory, question: str, user, limit: int = 5) -> list[dict]:
    """取该用户有权看到的最相似历史样例（动态 few-shot）。

    任何异常都吞掉返回空列表 —— 样例检索失败不该让问数本身失败。
    """
    from app.assistant import fewshot

    try:
        return fewshot.retrieve(session_factory, question, user, limit)
    except Exception:  # noqa: BLE001
        return []


def run_scoped_sql(pg_engine, sql: str, tables: list, max_rows: int,
                   timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
    """执行一条模型生成的 SQL，返回 (列名, 行)。

    抛 `SqlGuardError` 表示被安全闸门拦下（越权字段/非单条 SELECT）；
    其它异常表示执行失败。调用方自行区分。
    """
    validate_sql(sql, tables)
    scoped, params = rewrite_sql(sql, tables)
    return execute_readonly(pg_engine, scoped, params, max_rows, timeout_seconds)


def fetch_scoped_rows(pg_engine, dataset, grant, filters: dict, limit: int,
                      timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
    """按数据集 + 授权直接取数（不走模型 SQL），例如取样例或按条件预览。"""
    sql, params = build_scoped_sql(dataset, grant, filters, limit, 0)
    return execute_readonly(pg_engine, sql, params, limit, timeout_seconds)


def execute_readonly(pg_engine, sql: str, params: dict, max_rows: int,
                     timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
    """只读事务 + 语句超时 + 行数上限，三条硬约束都在这里。"""
    with pg_engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{int(timeout_seconds)}s'")
        result = conn.execute(text(sql), params)
        columns = list(result.keys())
        rows = [list(row) for row in result.fetchmany(max_rows + 1)]
    return columns, rows[:max_rows]


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SqlGuardError",
    "execute_readonly",
    "fetch_scoped_rows",
    "load_few_shots",
    "load_schema",
    "run_scoped_sql",
]
