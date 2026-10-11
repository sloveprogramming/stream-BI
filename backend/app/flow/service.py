"""流程的增删改查与版本管理。

三条策略，都服务于"这批数是怎么来的"这一个问题：

1. **只增不改**：每次保存写一个新版本，历史留档、可回滚，运行记录带版本号；
2. **保存即编译**：保存时就把 DSL 编译一次（编译内含权限检查），
   让"源数据集没授权""表达式写错"这类问题在**保存时**暴露，而不是等半夜跑批才炸。
   例外是**含 AI 算子的流程** —— 它的产物要等执行器跑完 Python 才存在，
   编译不出静态 SQL，所以保存时只做「参数 + 权限」校验（见 `_compile_for_save`）；
3. **可见性比数据集严**：编排是"生产"动作，比"消费"更敏感，
   所以不共用数据集那套授权模型 —— 管理员看全部，其他人只看自己的。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.flow.compile import CompiledFlow, compile_flow, compile_select_for, validate_flow
from app.flow.dsl import FlowDSL
from app.flow.errors import (
    FlowNeedsExecutionError,
    FlowNotFoundError,
    FlowPermissionError,
    FlowValidationError,
)
from app.models import (
    DataFlow,
    DataFlowVersion,
    FlowRun,
    User,
    new_version_id,
)


def _is_admin(user) -> bool:
    return bool(getattr(user, "is_admin", False))


def _uid(user):
    return getattr(user, "id", None)


# ---------- 查询 ----------


def visible_flows_query(session: Session, user):
    query = session.query(DataFlow)
    if not _is_admin(user):
        query = query.filter(DataFlow.owner_user_id == _uid(user))
    return query.order_by(DataFlow.id)


def list_flows(session: Session, user) -> list[DataFlow]:
    return visible_flows_query(session, user).all()


def get_flow(session: Session, flow_id: int, user) -> DataFlow:
    flow = session.get(DataFlow, flow_id)
    # 无权看到的流程统一当作"不存在"，不泄露存在性
    if flow is None or (not _is_admin(user) and flow.owner_user_id != _uid(user)):
        raise FlowNotFoundError(f"流程不存在：{flow_id}")
    return flow


def assert_can_edit(flow: DataFlow, user) -> None:
    if _is_admin(user) or flow.owner_user_id == _uid(user):
        return
    raise FlowPermissionError("只有流程 owner 或管理员可以修改该流程")


def _resolve_owner(session: Session, flow: DataFlow) -> User:
    owner = session.get(User, flow.owner_user_id)
    if owner is None or not owner.enabled:
        raise FlowPermissionError(
            f"流程 owner（id={flow.owner_user_id}）不存在或已停用，"
            f"无法确定取数口径，拒绝运行"
        )
    return owner


def get_version(session: Session, flow: DataFlow, version: int | None = None) -> DataFlowVersion:
    wanted = flow.current_version if version is None else version
    row = (
        session.query(DataFlowVersion)
        .filter_by(flow_id=flow.id, version=wanted)
        .first()
    )
    if row is None:
        raise FlowNotFoundError(f"流程 {flow.id} 不存在版本 {wanted}")
    return row


def list_versions(session: Session, flow: DataFlow) -> list[DataFlowVersion]:
    return (
        session.query(DataFlowVersion)
        .filter_by(flow_id=flow.id)
        .order_by(DataFlowVersion.version.desc())
        .all()
    )


# ---------- 写入 ----------


def _guard_size(dsl: FlowDSL, max_nodes: int | None) -> None:
    if max_nodes is not None and len(dsl.nodes) > max_nodes:
        raise FlowValidationError(
            f"流程节点数 {len(dsl.nodes)} 超过上限 {max_nodes}，请拆分后重试"
        )


def _compile_for_save(session: Session, dsl: FlowDSL, owner) -> CompiledFlow | None:
    """保存/校验阶段的编译。

    含 AI 算子的流程**编译不出静态 SQL** —— AI 节点不产 SQL，它的产物要等执行器
    跑完 Python 才存在。这不是错误，是这类流程固有的性质，所以降级为
    「参数与权限已校验」，返回 None 表示没有可落档的编译产物。

    降级**只针对这一种情况**：`compile_flow` 内部的权限解析发生在遍历节点之前，
    所以走到这里时「源数据集没授权」早已抛出；其它编译错误（表达式非法、
    引用不存在的列…）也照旧往外抛。
    """
    try:
        return compile_flow(dsl, session=session, owner=owner)
    except FlowNeedsExecutionError:
        return None


def _version_row(
    flow: DataFlow,
    version: int,
    dsl: FlowDSL,
    compiled: CompiledFlow | None,
    created_by: int | None,
) -> DataFlowVersion:
    """落一版。

    `compiled` 为 None 表示这条流程含 AI 算子、编译不出静态 SQL；
    此时 SQL 与血缘留空，等运行后由 `flow_runs` 补上。
    """
    return DataFlowVersion(
        flow_id=flow.id,
        version=version,
        version_id=new_version_id(),
        dsl=dsl.model_dump(mode="json"),
        compiled_sql=compiled.model_sql if compiled else "",
        compiled_yaml=compiled.schema_yml if compiled else "",
        dbt_selector=compiled.model_name if compiled else dsl.output.target_name,
        input_datasets=compiled.input_datasets if compiled else [],
        created_by=created_by,
    )


def create_flow(
    session: Session,
    *,
    dsl: FlowDSL,
    owner,
    max_nodes: int | None = None,
) -> DataFlow:
    """新建流程并落第 1 版。编译失败（含权限不足）会在这里就抛出，不落库。"""
    validate_flow(dsl)
    _guard_size(dsl, max_nodes)
    if session.query(DataFlow).filter_by(name=dsl.name).first() is not None:
        raise FlowValidationError(f"流程名已存在：{dsl.name}")

    compiled = _compile_for_save(session, dsl, owner)  # 先编译，失败就不建

    flow = DataFlow(
        name=dsl.name,
        title=dsl.title or dsl.name,
        description=dsl.description,
        owner_user_id=owner.id,
        current_version=1,
        target_schema=dsl.output.target_schema,
        target_table=dsl.output.target_name,
        enabled=True,
    )
    session.add(flow)
    session.flush()
    session.add(_version_row(flow, 1, dsl, compiled, created_by=_uid(owner)))
    session.commit()
    return flow


def save_version(
    session: Session,
    flow: DataFlow,
    *,
    dsl: FlowDSL,
    actor,
    max_nodes: int | None = None,
) -> DataFlowVersion:
    """保存为新版本（version + 1）。旧版本原样保留，可回滚。"""
    assert_can_edit(flow, actor)
    validate_flow(dsl)
    _guard_size(dsl, max_nodes)

    owner = _resolve_owner(session, flow)
    compiled = _compile_for_save(session, dsl, owner)

    flow.current_version += 1
    if dsl.title:
        flow.title = dsl.title
    flow.description = dsl.description
    flow.target_schema = dsl.output.target_schema
    flow.target_table = dsl.output.target_name

    row = _version_row(flow, flow.current_version, dsl, compiled, created_by=_uid(actor))
    session.add(row)
    session.commit()
    return row


def rollback(
    session: Session,
    flow: DataFlow,
    version: int,
    *,
    actor,
    max_nodes: int | None = None,
) -> DataFlowVersion:
    """回滚 = 拿旧版本的 DSL 再存一版新版本，历史一条不删。"""
    old = get_version(session, flow, version)
    return save_version(
        session, flow, dsl=FlowDSL(**old.dsl), actor=actor, max_nodes=max_nodes
    )


def set_enabled(session: Session, flow: DataFlow, enabled: bool, *, actor) -> DataFlow:
    assert_can_edit(flow, actor)
    flow.enabled = enabled
    session.commit()
    return flow


def preview_compile(session: Session, flow: DataFlow, dsl: FlowDSL) -> CompiledFlow:
    """只编译不执行，返回 SQL 与 YAML 供人查看（不落库、不改版本）。"""
    validate_flow(dsl)
    owner = _resolve_owner(session, flow)
    return compile_flow(dsl, session=session, owner=owner)


def preview_node(
    session: Session,
    flow: DataFlow,
    dsl: FlowDSL,
    node_id: str,
    *,
    pg_engine,
    limit: int = 20,
    timeout_seconds: int = 15,
) -> dict:
    """单步预览：只跑「到该节点为止」的链路，取前 N 行。

    这是拖拽编排最要紧的交互 —— 不能预览就等于盲编。
    取数走 `compile_select_for`（权限已内联）+ 只读事务 + 行数上限。

    注意：如果该节点上游有 AI 算子，因为 AI 结果表要执行时才物化，
    这里会报"需要先由 Python 执行并物化" —— 先整体跑一次即可。
    """
    from app.assistant.tools import execute_readonly

    validate_flow(dsl)
    owner = _resolve_owner(session, flow)
    sql = compile_select_for(dsl, node_id, session=session, owner=owner)
    columns, rows = execute_readonly(pg_engine, sql, {}, limit, timeout_seconds)
    return {"columns": columns, "rows": rows, "limit": limit, "sql": sql}


def validate_only(dsl: FlowDSL, *, max_nodes: int | None = None) -> None:
    """纯校验：不连库、不改状态。前端拖拽时高频调用。"""
    validate_flow(dsl)
    _guard_size(dsl, max_nodes)


def list_runs(session: Session, flow: DataFlow, *, limit: int = 20) -> list[FlowRun]:
    return (
        session.query(FlowRun)
        .filter_by(flow_id=flow.id)
        .order_by(FlowRun.id.desc())
        .limit(limit)
        .all()
    )
