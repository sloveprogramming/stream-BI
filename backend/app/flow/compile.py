"""把流程 DSL 编译成 dbt model（+ schema.yml）。

**权限红线就在这个文件里落地。** `source.dataset` 节点不会被编译成
`SELECT * FROM ods.xxx`，而是先经 `resolve_grant` + `build_scoped_sql`
生成「带行级权限 WHERE 的子查询」，再内联成 CTE。

为什么不用 dbt 的 `source()` / `ref()` 宏：它们无法携带 WHERE 条件，
而权限必须体现在 SQL 里（prompt 是软约束，这里是唯一硬约束）。
代价是 dbt 的静态血缘要靠 `flow_runs` + Dagster 资产图补齐；
收益是**编排在结构上不可能绕过权限** —— 算子层连裸表名都拿不到。

「一次编译，两个出口」：同一份 `model_sql`，
- 生产物化 → 交给 dbt 跑（`dbt build`，含 tests）；
- 单步预览 → 交给 DuckDB 在内存里跑（`LIMIT n`，不落表不污染 PG）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.access.grants import resolve_grant
from app.access.scoped_query import build_scoped_sql
from app.flow.context import CompileCtx
from app.flow.dsl import SOURCE_PREFIX, FlowDSL, FlowNode, topological_order
from app.flow.errors import FlowCompileError, FlowPermissionError, FlowValidationError
from app.flow.operators import get_operator
from app.flow.sqlutil import sql_literal
from app.metrics import MetricError, load_dictionary, load_for
from app.models import Dataset


@dataclass
class CompiledFlow:
    """一次编译的产物。"""

    model_sql: str  # dbt model 全文
    schema_yml: str  # 随附的 dbt schema.yml
    model_name: str  # dbt 模型名（= 文件名 = --select 值）
    target: str  # 限定表名，如 dwd.sales_order_clean
    output_columns: list[str] = field(default_factory=list)  # 产物列（注册数据集用）
    input_datasets: list[dict] = field(default_factory=list)  # 血缘上游
    grant_snapshot: dict[str, list] = field(default_factory=dict)  # 本次实际行权
    used_operators: list[str] = field(default_factory=list)


@dataclass
class _SourceInfo:
    node_id: str
    dataset_id: int
    dataset_name: str
    columns: list[str]
    row_rules: list
    scoped_sql: str


def _inline_params(sql: str, params: dict) -> str:
    """把绑定参数就地内联成字面量。

    dbt model 是**静态 SQL**，不能带 `:param` 绑定参数，所以这里必须内联。
    安全性由两件事保证：① 标识符经 `check_ident` 校验；② 值全部走
    `sql_literal`（单引号转义）。按 key 长度倒序替换，避免 `:r_1` 吃掉 `:r_10`。
    """
    for key in sorted(params, key=len, reverse=True):
        sql = sql.replace(f":{key}", sql_literal(params[key]))
    return sql


def _resolve_sources(dsl: FlowDSL, session, owner) -> dict[str, _SourceInfo]:
    """解析全部 source 节点：取授权、裁剪字段、生成带行权的 SELECT。"""
    out: dict[str, _SourceInfo] = {}
    for node in dsl.source_nodes():
        if node.type != "source.dataset":
            raise FlowCompileError(f"暂不支持的输入算子：{node.type}")
        params = node.params or {}
        if "dataset_id" not in params:
            raise FlowCompileError(f"输入节点 {node.id} 缺少 dataset_id")

        ds = session.get(Dataset, int(params["dataset_id"]))
        if ds is None or not ds.enabled:
            raise FlowCompileError(f"数据集不存在或已停用：{params['dataset_id']}")

        # ★ 必须用 resolve_grant（带管理员兜底）；用 get_effective_grant 管理员会被 403
        grant = resolve_grant(session, owner, ds.id)
        if grant is None:
            raise FlowPermissionError(
                f"流程 owner 对数据集「{ds.name}」没有授权，无法作为数据源。"
                f"请先给该用户授权（或在流程里换一个有权限的数据集）。"
            )

        # 字段白名单同样生效：列级权限在编排里也不能被绕过
        columns = list(grant.field_whitelist or ds.columns or [])
        # limit=None：物化需要全量，不能带 LIMIT
        scoped_sql, bind_params = build_scoped_sql(ds, grant, {}, None, 0)
        out[node.id] = _SourceInfo(
            node_id=node.id,
            dataset_id=ds.id,
            dataset_name=ds.name,
            columns=columns,
            row_rules=list(grant.row_rules or []),
            scoped_sql=_inline_params(scoped_sql, bind_params),
        )
    return out


def _input_columns_for(ctx: CompileCtx, dsl: FlowDSL, node: FlowNode) -> list[str] | None:
    ups = dsl.upstream_ids(node.id)
    if len(ups) != 1:
        return None
    return ctx.columns.get(ups[0])


def _indent(sql: str, spaces: int = 8) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in sql.splitlines())


def _render_schema_yml(dsl: FlowDSL, model_name: str, columns: list[str] | None) -> str:
    """生成 dbt schema.yml。

    刻意不引 YAML 库：这里结构固定且极浅，手写更可控（也少一个依赖）。
    描述里的引号统一替换掉，避免拼出非法 YAML。
    """
    desc = (dsl.description or dsl.title or dsl.name).replace('"', "'").replace("\n", " ")
    lines = [
        "version: 2",
        "",
        "# 由 BIsql 编排自动生成，请勿手改（下次编译会覆盖）",
        "models:",
        f"  - name: {model_name}",
        f'    description: "{desc}"',
    ]
    if columns:
        lines.append("    columns:")
        for col in columns:
            lines.append(f"      - name: {col}")
    return "\n".join(lines) + "\n"


def validate_flow(dsl: FlowDSL) -> None:
    """轻量预检：不连数据库，只确认每个节点的算子存在、参数能解析。

    给「保存前校验」和「LLM 生成流程后的自检」用；真正能不能跑还要看权限，
    那部分在 `compile_flow` 里。

    指标引用额外对一遍字典（只读文件、不连库）：未知指标属于**配置写错了**，
    应当和"参数不合法"一样在最早的地方暴露，不该等到编译/执行。
    """
    for node in dsl.nodes:
        get_operator(node.type).parse(node)  # 未知算子 / 参数非法都会在这抛
    _check_metric_refs(dsl)


def _check_metric_refs(dsl: FlowDSL) -> None:
    """把流程里引用的指标名拿出来对一遍字典。没引用指标就完全不读文件。"""
    names = [
        (node.params or {}).get("metric")
        for node in dsl.nodes
        if node.type == "field.derive"
    ]
    names = [n for n in names if n]
    if not names:
        return
    try:
        dictionary = load_dictionary()
        for name in names:
            dictionary.get(name)
    except MetricError as exc:
        raise FlowValidationError(str(exc)) from exc


def _ancestors(dsl: FlowDSL, node_id: str) -> set[str]:
    """node_id 的全部上游（含间接），用于按需裁剪编译范围。"""
    out: set[str] = set()
    stack = list(dsl.upstream_ids(node_id))
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(dsl.upstream_ids(current))
    return out


@dataclass
class _BuildResult:
    ctx: CompileCtx
    ctes: list[str]
    used: list[str]
    sources: dict[str, _SourceInfo]


def _build_ctes(
    dsl: FlowDSL, *, session, owner, upto: str | None = None
) -> _BuildResult:
    """按拓扑序把节点编译成 CTE 列表。

    `upto` 指定时只编到该节点（含它的全部上游），用于 AI 算子取上游数据 ——
    它需要的是"到这一步为止的结果"，不是整条流程。

    `sources` 一并返回：授权快照必须和真正生成 SQL 的那一次解析是**同一份**，
    否则审计记录里的行权口径可能和实际执行的 SQL 不一致。
    """
    order = topological_order(dsl.nodes, dsl.edges)
    keep = {upto, *_ancestors(dsl, upto)} if upto is not None else None

    # 指标字典带上库里的数据集做完整校验：引用了不存在的列会在这一步就报错
    ctx = CompileCtx(dsl=dsl, metrics=load_for(session))
    sources = _resolve_sources(dsl, session, owner)
    ctx.source_loader = lambda node: sources[node.id].scoped_sql
    for node_id, info in sources.items():
        # 源节点的列先填好，供下游 rename/cast 这类算子使用
        ctx.columns[node_id] = info.columns

    ctes: list[str] = []
    used: list[str] = []
    for node in order:
        if keep is not None and node.id not in keep:
            continue
        op = get_operator(node.type)
        used.append(node.type)

        if op.engine == "python":
            # AI 算子不产 SQL：它由 runner 先跑 Python、物化结果，
            # 再把表名写回 node.params['materialized_table']，这里只引用。
            sql = ctx.external_source(node)
        else:
            sql = op.compile_sql(ctx, node)

        # 源节点的列已在上面预置；其余节点由算子声明"输出哪些列"，沿 DAG 往下传
        if not node.type.startswith(SOURCE_PREFIX):
            ctx.columns[node.id] = op.output_columns(
                ctx, node, _input_columns_for(ctx, dsl, node)
            )
        ctes.append(f"{ctx.cte(node.id)} AS (\n{_indent(sql)}\n    )")
    return _BuildResult(ctx=ctx, ctes=ctes, used=used, sources=sources)


def compile_select_for(dsl: FlowDSL, node_id: str, *, session, owner) -> str:
    """编译出「到 node_id 为止」的一条 SELECT。

    AI 算子靠它取上游数据：执行这条 SELECT 拿到行，喂给模型，
    再把结果物化成表供下游 dbt 模型引用。权限同样由 `_resolve_sources` 套好，
    所以 AI 算子也看不到自己没权限的行。
    """
    if node_id not in dsl.node_map():
        raise FlowCompileError(f"节点不存在：{node_id}")
    built = _build_ctes(dsl, session=session, owner=owner, upto=node_id)
    return f"WITH {',\n'.join(built.ctes)}\nSELECT * FROM {built.ctx.cte(node_id)}\n"


def compile_flow(dsl: FlowDSL, *, session, owner) -> CompiledFlow:
    """把 DSL 编译成 dbt model。`owner` 决定源数据的行级与字段级口径。"""
    built = _build_ctes(dsl, session=session, owner=owner)
    ctx, ctes, used, sources = built.ctx, built.ctes, built.used, built.sources

    model_name = dsl.output.target_name
    target = f"{dsl.output.target_schema}.{model_name}"
    header = (
        "{{ config(materialized='%s', schema='%s', alias='%s') }}\n"
        "-- 由 BIsql 编排生成（flow=%s），请勿手改：下次编译会覆盖\n"
        % (dsl.output.materialization, dsl.output.target_schema, model_name, dsl.name)
    )
    body = ",\n".join(ctes)
    model_sql = f"{header}WITH {body}\nSELECT * FROM {ctx.cte(dsl.sink.id)}\n"

    sink_cols = ctx.columns.get(dsl.sink.id) or []
    return CompiledFlow(
        model_sql=model_sql,
        schema_yml=_render_schema_yml(dsl, model_name, sink_cols),
        model_name=model_name,
        target=target,
        output_columns=list(sink_cols),
        input_datasets=sorted(
            [{"id": s.dataset_id, "name": s.dataset_name} for s in sources.values()],
            key=lambda d: d["id"],
        ),
        grant_snapshot={s.dataset_name: s.row_rules for s in sources.values()},
        used_operators=sorted(set(used)),
    )
