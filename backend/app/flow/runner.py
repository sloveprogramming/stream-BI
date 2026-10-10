"""流程执行：编译落盘 → dbt build → 产物注册为数据集。

执行链路：

    取当前版本 DSL → compile_flow（内含权限裁剪）→ 写 dbt model 到 models/generated/
    → dbt build（含 tests）→ 数行数 → 注册数据集 + 建授权

**产物权限的两条铁律**（与 compile.py 的"读源必过裁剪器"合起来构成完整的权限闭环）：

1. 产物继承来源行的行级规则 —— owner 只能看到自己被授权的那部分，
   物化出来的表自然也只有那部分；
2. 若某条行级规则的字段在产物里**不存在**（例如按 warehouse 限权、
   但流程里把 warehouse 列丢了），则**禁止分享**该数据集，强制 owner-only。
   不这么做的话，owner 可以把自己看不到全貌的数据集分享出去，等于开了后门。
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.flow.compile import CompiledFlow, compile_flow
from app.flow.dsl import FlowDSL
from app.flow.errors import FlowError, FlowExecutionError, FlowPermissionError
from app.flow.service import get_version, _resolve_owner
from app.models import (
    Dataset,
    DatasetGrant,
    DataFlow,
    FlowRun,
    new_flow_run_id,
)

_APP_DIR = Path(__file__).resolve().parents[1]  # backend/app
_BACKEND_DIR = _APP_DIR.parent  # backend


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DbtRunResult:
    success: bool
    message: str = ""


def resolve_project_dir(project_dir: str) -> Path:
    """dbt 项目目录：相对路径按 backend/ 解析，不依赖进程 CWD。"""
    path = Path(project_dir)
    return path if path.is_absolute() else (_BACKEND_DIR / path)


# ---------- 写文件 ----------


def write_dbt_files(project_dir: str, generated_subdir: str, compiled: CompiledFlow) -> list[Path]:
    """把编译产物写成 dbt model + schema.yml。

    落在 `models/generated/`：这个目录由流程覆盖写入，**不要手改**
    （文件头已写明）。要写自己的逻辑请放 `models/staging/` 或 `models/marts/`。
    """
    target_dir = resolve_project_dir(project_dir) / generated_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    model_path = target_dir / f"{compiled.model_name}.sql"
    yml_path = target_dir / f"{compiled.model_name}.yml"
    model_path.write_text(compiled.model_sql, encoding="utf-8")
    yml_path.write_text(compiled.schema_yml, encoding="utf-8")
    return [model_path, yml_path]


# ---------- 执行 ----------


def run_dbt(project_dir: str, model_name: str, *, target: str) -> DbtRunResult:
    """调 dbt 编译并物化。用官方 `dbtRunner` 进程内 API，不起子进程。"""
    try:
        from dbt.cli.main import dbtRunner
    except ImportError:
        return DbtRunResult(
            False,
            "dbt 未安装，无法物化。请先安装：pip install dbt-core dbt-postgres",
        )

    pdir = resolve_project_dir(project_dir)
    args = [
        "build",
        "--select", model_name,
        "--project-dir", str(pdir),
        "--profiles-dir", str(pdir),
        "--target", target,
    ]
    try:
        result = dbtRunner().invoke(args)
    except Exception as exc:  # noqa: BLE001 —— dbt 内部异常类型不稳定
        return DbtRunResult(False, f"dbt 调用异常：{type(exc).__name__}: {exc}")

    if getattr(result, "success", False):
        return DbtRunResult(True, "")
    exc = getattr(result, "exception", None)
    message = str(exc) if exc else "dbt build 失败（详见 backend/dbt/logs/）"
    return DbtRunResult(False, message[:4000])


def _count_rows(pg_engine, qualified_table: str) -> int:
    schema, table = qualified_table.split(".", 1)
    with pg_engine.connect() as conn:
        return int(
            conn.execute(
                text(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            ).scalar()
            or 0
        )


def _introspect_columns(pg_engine, qualified_table: str) -> list[str]:
    """从库里读真实列 —— 这是产物的"事实来源"，比编译期推导更可信。

    查不到列**必须报错**，不能返回空列表：空列表会被下游当成"产物没有任何列"，
    进而把所有行级规则判成"无法继承"。那是把"系统异常"错当成"业务事实"，
    会静默改变权限判定结果。
    """
    schema, table = qualified_table.split(".", 1)
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t "
                "ORDER BY ordinal_position"
            ),
            {"s": schema, "t": table},
        )
        columns = [r[0] for r in rows]
    if not columns:
        raise FlowExecutionError(
            f"物化表 {qualified_table} 不存在或没有列，但 dbt 报告成功。"
            f"请检查 dbt 日志与生成模型里的 schema 配置。"
        )
    return columns


# ---------- 产物注册与授权继承 ----------


def _split_rules(compiled: CompiledFlow, columns: list[str]):
    """把来源行权规则分成「能继承」与「产物里没有该列」两堆。"""
    known = set(columns)
    inheritable: list[dict] = []
    blocked: list[dict] = []
    seen: set[tuple] = set()
    for dataset_name, rules in (compiled.grant_snapshot or {}).items():
        for rule in rules or []:
            key = tuple(sorted(rule.items()))
            if key in seen:
                continue
            seen.add(key)
            item = {**rule, "from_dataset": dataset_name}
            (inheritable if rule.get("field") in known else blocked).append(item)
    return inheritable, blocked


def register_output_dataset(
    session,
    flow: DataFlow,
    compiled: CompiledFlow,
    dsl: FlowDSL,
    owner,
    pg_engine,
) -> tuple[Dataset | None, list[dict]]:
    """把产物上架成数据集，并按规则重建授权。返回 (数据集, 告警列表)。"""
    if not dsl.output.register_dataset:
        return None, []

    columns = _introspect_columns(pg_engine, compiled.target)

    name = dsl.output.target_name
    ds = session.query(Dataset).filter_by(name=name).first()
    if ds is None:
        ds = Dataset(name=name, enabled=True)
        session.add(ds)
    ds.title = dsl.title or name
    ds.description = dsl.description or (flow.description or "")
    ds.source_table = compiled.target
    ds.columns = columns or ds.columns or []
    ds.pk_columns = list(dsl.output.pk_columns or ds.pk_columns or [])
    session.flush()

    rules, blocked = _split_rules(compiled, ds.columns)
    # 规则里的 from_dataset 只是给人看的溯源信息，不入库（授权规则只认 field/op/value）
    stored_rules = [{k: v for k, v in r.items() if k in ("field", "op", "value")} for r in rules]

    warnings: list[dict] = []
    share_with = list(dsl.output.share_with or [])
    if blocked:
        warnings.append(
            {
                "kind": "row_rule_not_inheritable",
                "message": "存在无法继承到产物的行级规则，已强制该数据集仅 owner 可见（禁止分享）",
                "blocked": blocked,
            }
        )
        share_with = []

    # 重建授权：产物默认只对 owner 开放
    session.query(DatasetGrant).filter_by(dataset_id=ds.id).delete()
    session.add(
        DatasetGrant(
            user_id=owner.id, dataset_id=ds.id, row_rules=stored_rules, field_whitelist=None
        )
    )
    for user_id in share_with:
        session.add(
            DatasetGrant(
                user_id=user_id, dataset_id=ds.id, row_rules=stored_rules, field_whitelist=None
            )
        )
    session.flush()
    return ds, warnings


# ---------- 主入口 ----------


def run_flow(
    session,
    flow: DataFlow,
    *,
    pg_engine,
    settings,
    triggered_by: str = "manual",
    trigger_user_id: int | None = None,
    dbt_runner=run_dbt,
) -> FlowRun:
    """执行一条流程，返回运行记录（无论成功失败都落库）。"""
    version = get_version(session, flow)
    dsl = FlowDSL(**version.dsl)
    owner = _resolve_owner(session, flow)

    run = FlowRun(
        flow_id=flow.id,
        version=version.version,
        run_id=new_flow_run_id(),
        triggered_by=triggered_by,
        status="running",
        trigger_user_id=trigger_user_id,
    )
    session.add(run)
    session.flush()

    started = time.perf_counter()
    meta: dict = {}
    try:
        # 编译内含权限裁剪：owner 对源数据集没授权，这里就中止
        compiled = compile_flow(dsl, session=session, owner=owner)
        run.grant_snapshot = compiled.grant_snapshot
        meta["target"] = compiled.target
        meta["operators"] = compiled.used_operators

        paths = write_dbt_files(
            settings.flow_dbt_project_dir, settings.flow_generated_subdir, compiled
        )
        meta["generated_files"] = [str(p) for p in paths]

        result = dbt_runner(
            settings.flow_dbt_project_dir, compiled.model_name, target=settings.flow_dbt_target
        )
        meta["dbt"] = {"success": result.success, "message": (result.message or "")[:2000]}

        if not result.success:
            run.status = "failed"
            run.error = result.message or "dbt build 失败"
        else:
            ds, warnings = register_output_dataset(
                session, flow, compiled, dsl, owner, pg_engine
            )
            if ds is not None:
                flow.output_dataset_id = ds.id
                meta["dataset"] = {"id": ds.id, "name": ds.name, "columns": len(ds.columns or [])}
            if warnings:
                meta["warnings"] = warnings
            run.rows_out = _count_rows(pg_engine, compiled.target)
            run.status = "ok"
    except FlowPermissionError as exc:
        # 权限不足与"跑挂了"要区分开：前者是预期内的拒绝，不该触发重试告警
        run.status = "aborted"
        run.error = str(exc)
    except FlowError as exc:
        run.status = "failed"
        run.error = str(exc)
    except Exception as exc:  # noqa: BLE001 —— 兜底，保证运行记录一定落库
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        # 非预期异常保留堆栈：排障时不用重跑一遍
        meta["traceback"] = traceback.format_exc()[-4000:]
    finally:
        run.meta = meta
        run.elapsed_ms = int((time.perf_counter() - started) * 1000)
        run.finished_at = _utcnow()
        session.add(run)
        session.commit()

    if run.status == "running":  # 理论不可达，防御性兜底
        raise FlowExecutionError("运行状态未收敛，请检查运行记录")
    return run
