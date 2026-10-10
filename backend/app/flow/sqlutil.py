"""编排层拼 SQL 的公共工具。

两件事刻意复用现成的，不新造：

1. **标识符校验**用权限裁剪器里的 `check_ident`。仓库里已经有
   `access/scoped_query.py::check_ident` 和 `sync/spec.py::quote_ident` 两处，
   新代码一律走这里，避免出现第三套（多套校验迟早会不一致）。
2. **表达式校验**用 `sqlglot`（项目已依赖，问数链路的 AST 闸门也用它）。
"""
import re

import sqlglot

from app.access.scoped_query import check_ident
from app.flow.errors import FlowCompileError, FlowValidationError

_NON_IDENT = re.compile(r"[^0-9A-Za-z_]")


def q(name: str) -> str:
    """引用一个标识符：先校验（非法即抛），再加双引号。"""
    return '"' + check_ident(name) + '"'


def q_alias(name: str) -> str:
    """引用一个**算子新生成的列名**（rename / derive / window 的结果列）。

    沿用项目既有的 ASCII 标识符策略（`check_ident`），不另开一套：
    物理列名保持 ASCII，**中文展示名请填到数据集的字段注释里**
    （`datasets.column_comments`）—— 那样它才会同时出现在问数 schema 上下文
    和 dbt 文档中，而不是只在这张物理表里。

    不放进物理列名的第二个原因：别名会沿 DAG 往下传给后续算子，
    下游引用时同样要过 `check_ident`，允许中文会让链路中途断掉。
    """
    try:
        return q(name)
    except ValueError as exc:
        raise FlowCompileError(
            f"列名 {name!r} 不合法：物理列名只允许字母、数字、下划线，且不以数字开头。"
            f"如果这是要给人看的中文名，请填到数据集的字段注释里（不是物理列名）。"
        ) from exc


def qlist(names) -> str:
    """把一组标识符拼成 `"a", "b"` 形式。"""
    return ", ".join(q(n) for n in names)


def sanitize_ident(raw: str) -> str:
    """把任意字符串规范成合法标识符片段（用于由节点 id 派生 CTE 名）。

    节点 id 由画布生成（可能是 `1`、`node-3`），不能直接当 SQL 片段，
    所以统一规范化；同时 `FlowDSL` 会校验规范化后不重复。
    """
    s = _NON_IDENT.sub("_", raw or "")
    if not s or s[0].isdigit():
        s = "n" + s
    return s


def sql_literal(value) -> str:
    """把 Python 值安全地转成 SQL 字面量（单引号转义，拒绝可疑类型）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple, set)):
        if not value:
            return "NULL"
        return "(" + ", ".join(sql_literal(v) for v in value) + ")"
    s = str(value).replace("'", "''")
    return f"'{s}'"


def validate_expr(expr: str) -> str:
    """校验一段 SQL 表达式：能解析、且是单一表达式。

    用户（以及 LLM）会在 `clean.filter` / `field.derive` 里写原生表达式，
    这里不做「黑名单过滤」（防不住），而是用 sqlglot 解析 + 禁止分号，
    挡住"多条语句堆叠"这类最常见的绕过手法。
    """
    if not expr or not expr.strip():
        raise FlowValidationError("表达式不能为空")
    if ";" in expr:
        raise FlowValidationError(f"表达式不得包含分号: {expr!r}")
    try:
        parsed = sqlglot.parse_one(f"SELECT {expr}", read="postgres")
    except Exception as exc:  # noqa: BLE001 —— sqlglot 的异常类型不稳定
        raise FlowValidationError(f"表达式无法解析: {expr!r}（{exc}）") from exc
    if parsed is None:
        raise FlowValidationError(f"表达式无法解析: {expr!r}")
    return expr
