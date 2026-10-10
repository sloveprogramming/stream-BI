"""数据编排（flow）的 REST 接口。

约定：
- 请求体直接收 DSL（`{"dsl": {...}}`），DSL 结构见 `app/flow/dsl.py`；
- 错误按类型映射：404 不存在 / 403 无权限 / 400 DSL 或编译问题 / 500 其它；
- 运行是**同步**的（dbt build 在请求里跑完）。当前规模够用；
  流程变重之后应交给 Dagster 异步跑，接口签名不用改。
"""
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.flow import service
from app.flow.dsl import FlowDSL
from app.flow.errors import (
    FlowCompileError,
    FlowError,
    FlowNotFoundError,
    FlowPermissionError,
    FlowValidationError,
)
from app.flow.operators import operator_catalog
from app.flow.runner import run_flow
from app.models import AuditLog

router = APIRouter(prefix="/flows", tags=["flows"])


class FlowBody(BaseModel):
    dsl: dict = Field(description="流程 DSL，结构见 app/flow/dsl.py")


class PreviewBody(FlowBody):
    node_id: str = Field(description="要预览的节点 id")
    limit: int = Field(default=20, ge=1, le=200)


@contextmanager
def _api_errors():
    """把编排层异常翻译成 HTTP 状态码。权限问题与参数问题必须区分开。"""
    try:
        yield
    except FlowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FlowPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (FlowValidationError, FlowCompileError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FlowError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _flow_out(flow, versions: list | None = None) -> dict:
    return {
        "id": flow.id,
        "name": flow.name,
        "title": flow.title,
        "description": flow.description,
        "owner_user_id": flow.owner_user_id,
        "current_version": flow.current_version,
        "target": f"{flow.target_schema}.{flow.target_table}",
        "output_dataset_id": flow.output_dataset_id,
        "enabled": flow.enabled,
        "created_at": flow.created_at.isoformat() if flow.created_at else None,
        "updated_at": flow.updated_at.isoformat() if flow.updated_at else None,
        "version_count": len(versions) if versions is not None else None,
    }


def _version_out(version, include_dsl: bool = False) -> dict:
    data = {
        "version": version.version,
        "version_id": version.version_id,
        "dbt_selector": version.dbt_selector,
        "input_datasets": version.input_datasets,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if include_dsl:
        data["dsl"] = version.dsl
        data["compiled_sql"] = version.compiled_sql
        data["compiled_yaml"] = version.compiled_yaml
    return data


def _run_out(run) -> dict:
    return {
        "run_id": run.run_id,
        "version": run.version,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "rows_out": run.rows_out,
        "elapsed_ms": run.elapsed_ms,
        "error": run.error,
        "grant_snapshot": run.grant_snapshot,
        "meta": run.meta,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/operators")
def list_operators(principal: AnyPrincipal = Depends(get_any_principal)):
    """算子目录：前端算子面板与配置表单都由它驱动（含 JSON Schema）。"""
    return {"categories": _categories(), "operators": operator_catalog()}


def _categories() -> list[str]:
    seen: list[str] = []
    for op in operator_catalog():
        if op["category"] not in seen:
            seen.append(op["category"])
    return seen


@router.get("")
def list_flows(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flows = service.list_flows(session, principal.user)
        return [_flow_out(f, service.list_versions(session, f)) for f in flows]


@router.post("", status_code=201)
def create_flow(
    body: FlowBody,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.create_flow(
            session,
            dsl=FlowDSL(**body.dsl),
            owner=principal.user,
            max_nodes=ctx.settings.flow_max_nodes,
        )
        return _flow_out(flow)


@router.get("/{flow_id}")
def get_flow(
    flow_id: int,
    include_dsl: bool = True,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        data = _flow_out(flow, service.list_versions(session, flow))
        if include_dsl:
            data["current"] = _version_out(service.get_version(session, flow), include_dsl=True)
        return data


@router.put("/{flow_id}")
def save_flow(
    flow_id: int,
    body: FlowBody,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        version = service.save_version(
            session,
            flow,
            dsl=FlowDSL(**body.dsl),
            actor=principal.user,
            max_nodes=ctx.settings.flow_max_nodes,
        )
        return {"flow": _flow_out(flow), "version": _version_out(version)}


@router.post("/{flow_id}/validate")
def validate_flow_dsl(
    flow_id: int,
    body: FlowBody,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """只校验：不落库、不改版本。前端拖拽时可以频繁调。"""
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        dsl = FlowDSL(**body.dsl)
        service.validate_only(dsl, max_nodes=ctx.settings.flow_max_nodes)
        compiled = service.preview_compile(session, flow, dsl)
        return {
            "ok": True,
            "operators": compiled.used_operators,
            "input_datasets": compiled.input_datasets,
            "target": compiled.target,
        }


@router.post("/{flow_id}/compile")
def compile_flow_only(
    flow_id: int,
    body: FlowBody,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """只编译不执行：把生成的 SQL / schema.yml 给人看。"""
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        compiled = service.preview_compile(session, flow, FlowDSL(**body.dsl))
        return {
            "model_name": compiled.model_name,
            "target": compiled.target,
            "model_sql": compiled.model_sql,
            "schema_yml": compiled.schema_yml,
            "input_datasets": compiled.input_datasets,
            "grant_snapshot": compiled.grant_snapshot,
            "used_operators": compiled.used_operators,
        }


@router.post("/{flow_id}/preview")
def preview_node(
    flow_id: int,
    body: PreviewBody,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """单步预览：跑「到该节点为止」的链路，取前 N 行。拖拽时最常用的接口。"""
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        return service.preview_node(
            session,
            flow,
            FlowDSL(**body.dsl),
            body.node_id,
            pg_engine=ctx.pg_engine,
            limit=body.limit,
        )


@router.post("/{flow_id}/run")
def run_flow_now(
    flow_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        run = run_flow(
            session,
            flow,
            pg_engine=ctx.pg_engine,
            settings=ctx.settings,
            triggered_by="manual",
            trigger_user_id=principal.user.id,
        )
        session.add(
            AuditLog(
                user_id=principal.user.id,
                channel="flow",
                dataset=flow.target_table,
                action="run",
                row_count=run.rows_out,
                duration_ms=run.elapsed_ms,
                detail={"flow_id": flow.id, "run_id": run.run_id, "status": run.status},
            )
        )
        session.commit()
        return _run_out(run)


@router.get("/{flow_id}/runs")
def list_runs(
    flow_id: int,
    limit: int = 20,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        rows = service.list_runs(session, flow, limit=limit)
        return [_run_out(r) for r in rows]


@router.get("/{flow_id}/versions")
def list_versions(
    flow_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        return [_version_out(v) for v in service.list_versions(session, flow)]


@router.get("/{flow_id}/versions/{version}")
def get_version(
    flow_id: int,
    version: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        return _version_out(service.get_version(session, flow, version), include_dsl=True)


@router.post("/{flow_id}/rollback/{version}")
def rollback(
    flow_id: int,
    version: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """回滚 = 用旧版本内容再存一版，历史一条不删。"""
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        new_version = service.rollback(
            session,
            flow,
            version,
            actor=principal.user,
            max_nodes=ctx.settings.flow_max_nodes,
        )
        return {"flow": _flow_out(flow), "version": _version_out(new_version)}


@router.post("/{flow_id}/enable")
def set_enabled(
    flow_id: int,
    enabled: bool = True,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with _api_errors(), ctx.session_factory() as session:
        flow = service.get_flow(session, flow_id, principal.user)
        return _flow_out(service.set_enabled(session, flow, enabled, actor=principal.user))
