"""流程执行：产物注册 + **行权继承**。

不依赖 dbt：注入一个假的 dbt runner，它按 table 建表并灌数据，
这样行数统计、列探查、授权重建这几步走的是真实代码路径。

注意 `dbt_runner(project_dir, model_name, target=...)` 里的 `target`
是 **dbt profile 的 target 名**（prod），不是物化表名 —— 表名由编译产物决定。
"""
from pathlib import Path

import sqlalchemy as sa

from app.flow import service
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.runner import DbtRunResult, resolve_project_dir, run_flow
from app.models import Dataset, DatasetGrant, FlowRun

ROW_RULE = {"field": "warehouse", "op": "eq", "value": "德清"}


class _Settings:
    flow_dbt_project_dir = "dbt"
    flow_generated_subdir = "models/generated"
    flow_dbt_target = "prod"
    flow_max_nodes = 50


class FakeDbt:
    """假的 dbt：按 table 建表灌数据，顺便记录被调用参数。"""

    def __init__(
        self,
        engine,
        table,
        *,
        columns=("order_id", "warehouse"),
        rows=(("1", "德清"), ("2", "德清")),
        success=True,
        message="boom",
    ):
        self.engine = engine
        self.table = table
        self.columns = columns
        self.rows = rows
        self.success = success
        self.message = message
        self.calls: list[tuple] = []

    def __call__(self, project_dir, model_name, *, target):
        self.calls.append((project_dir, model_name, target))
        if not self.success:
            return DbtRunResult(False, self.message)
        schema, name = self.table.split(".")
        cols_sql = ", ".join(f'"{c}" text' for c in self.columns)
        with self.engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}"."{name}"'))
            conn.execute(sa.text(f'CREATE TABLE "{schema}"."{name}" ({cols_sql})'))
            for row in self.rows:
                holders = ", ".join(f":v{i}" for i in range(len(row)))
                conn.execute(
                    sa.text(f'INSERT INTO "{schema}"."{name}" VALUES ({holders})'),
                    {f"v{i}": v for i, v in enumerate(row)},
                )
        return DbtRunResult(True, "")


def make_dsl(env, *, select=None, share_with=None):
    suffix = env["suffix"]
    nodes = [FlowNode(id="src", type="source.dataset", params={"dataset_id": env["ds"]})]
    edges = []
    prev = "src"
    if select:
        nodes.append(FlowNode(id="pick", type="field.select", params={"columns": select}))
        edges.append(FlowEdge(source=prev, target="pick"))
        prev = "pick"
    nodes.append(FlowNode(id="out", type="sink.materialize", params={}))
    edges.append(FlowEdge(source=prev, target="out"))
    return FlowDSL(
        name=f"flowt_{suffix}",
        title="测试流程",
        nodes=nodes,
        edges=edges,
        output=FlowOutput(
            target_name=f"flowt_{suffix}",
            target_schema="dwd",
            pk_columns=["order_id"],
            share_with=share_with or [],
        ),
    )


def _fake(env, engine, **kw):
    return FakeDbt(engine, f"dwd.flowt_{env['suffix']}", **kw)


def _prepare(sf, env, load_user, dsl):
    with sf() as s:
        return service.create_flow(s, dsl=dsl, owner=load_user(env["owner"])).id


def _grants(sf, dataset_id):
    with sf() as s:
        return s.query(DatasetGrant).filter_by(dataset_id=dataset_id).all()


def test_run_success_registers_dataset_and_inherits_row_rules(flow_env, load_user):
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))
    fake = _fake(env, engine)

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        run = run_flow(
            s, flow, pg_engine=engine, settings=_Settings(),
            trigger_user_id=env["owner"], dbt_runner=fake,
        )

        assert run.status == "ok", run.error + "\n" + str(run.meta.get("traceback"))
        assert run.rows_out == 2
        assert run.grant_snapshot == {env["ds_name"]: [ROW_RULE]}
        assert fake.calls[0][1] == f"flowt_{env['suffix']}"  # --select 用模型名
        assert fake.calls[0][2] == "prod"  # target 是 profile 名，不是表名

        ds = s.get(Dataset, flow.output_dataset_id)
        assert ds.source_table == f"dwd.flowt_{env['suffix']}"
        assert ds.columns == ["order_id", "warehouse"]  # 从库里探查出来的真实列
        assert ds.pk_columns == ["order_id"]
        assert run.meta["dataset"]["name"] == ds.name

        grants = _grants(sf, ds.id)
        assert len(grants) == 1  # 默认只有 owner
        assert grants[0].user_id == env["owner"]
        # ★ 产物必须继承来源的行级规则，否则等于把权限洗掉了
        assert grants[0].row_rules == [ROW_RULE]


def test_run_failure_marks_failed_and_registers_nothing(flow_env, load_user):
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))
    fake = _fake(env, engine, success=False, message="compilation error")

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        before = flow.output_dataset_id
        run = run_flow(s, flow, pg_engine=engine, settings=_Settings(), dbt_runner=fake)

        assert run.status == "failed"
        assert "compilation error" in run.error
        assert flow.output_dataset_id == before
        assert run.meta["dbt"] == {"success": False, "message": "compilation error"}
        assert s.query(Dataset).filter_by(name=f"flowt_{env['suffix']}").first() is None


def test_run_aborted_when_owner_has_no_grant(flow_env, load_user):
    """owner 无授权 → 状态是 aborted（预期内的拒绝），不是 failed（跑挂了）。"""
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))
    fake = _fake(env, engine)

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        flow.owner_user_id = env["other"]  # 换成一个没有授权的人
        s.commit()
        run = run_flow(s, flow, pg_engine=engine, settings=_Settings(), dbt_runner=fake)

        assert run.status == "aborted"
        assert "没有授权" in run.error
        assert fake.calls == []  # 根本没走到 dbt


def test_row_rule_not_inheritable_forces_owner_only(flow_env, load_user):
    """产物里没有 warehouse 列 → 行级规则无法继承 → 禁止分享，强制 owner-only。

    不这么做的话，owner 可以把自己看不到全貌的数据集分享出去，等于开后门。
    """
    env, sf, engine = flow_env
    dsl = make_dsl(env, select=["order_id"], share_with=[env["other"]])
    flow_id = _prepare(sf, env, load_user, dsl)
    fake = _fake(env, engine, columns=("order_id",), rows=(("1",),))

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        run = run_flow(s, flow, pg_engine=engine, settings=_Settings(), dbt_runner=fake)

        assert run.status == "ok", run.error + "\n" + str(run.meta.get("traceback"))
        warnings = run.meta["warnings"]
        assert warnings[0]["kind"] == "row_rule_not_inheritable"
        assert warnings[0]["blocked"][0]["field"] == "warehouse"

        grants = _grants(sf, flow.output_dataset_id)
        assert [g.user_id for g in grants] == [env["owner"]]  # share_with 被忽略


def test_share_with_grants_others_when_rules_inheritable(flow_env, load_user):
    env, sf, engine = flow_env
    dsl = make_dsl(env, share_with=[env["other"]])
    flow_id = _prepare(sf, env, load_user, dsl)
    fake = _fake(env, engine)

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        run = run_flow(s, flow, pg_engine=engine, settings=_Settings(), dbt_runner=fake)

        assert run.status == "ok", run.error + "\n" + str(run.meta.get("traceback"))
        assert "warnings" not in run.meta
        grants = _grants(sf, flow.output_dataset_id)
        assert sorted(g.user_id for g in grants) == sorted([env["owner"], env["other"]])
        # 被分享的人拿到的也是裁剪后的规则，不是全量
        assert all(g.row_rules == [ROW_RULE] for g in grants)


def test_rerun_updates_dataset_without_duplicating(flow_env, load_user):
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        run_flow(s, flow, pg_engine=engine, settings=_Settings(),
                 dbt_runner=_fake(env, engine, rows=(("1", "德清"),)))
        first_total = s.query(FlowRun).filter_by(flow_id=flow_id).count()
        run_flow(s, flow, pg_engine=engine, settings=_Settings(),
                 dbt_runner=_fake(env, engine, rows=(("1", "德清"), ("2", "德清"), ("3", "安徽"))))
        second = s.query(FlowRun).filter_by(flow_id=flow_id).order_by(FlowRun.id.desc()).first()

        assert s.query(FlowRun).filter_by(flow_id=flow_id).count() == first_total + 1
        assert second.status == "ok", second.error
        assert second.rows_out == 3
        # 数据集只有一份，授权也只有一份（重建而非追加）
        assert s.query(Dataset).filter_by(name=f"flowt_{env['suffix']}").count() == 1
        assert len(_grants(sf, flow.output_dataset_id)) == 1


def test_generated_files_land_in_generated_dir(flow_env, load_user):
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        run = run_flow(
            s, flow, pg_engine=engine, settings=_Settings(), dbt_runner=_fake(env, engine)
        )
        paths = run.meta["generated_files"]

    assert len(paths) == 2
    assert any(p.endswith(".sql") for p in paths)
    assert any(p.endswith(".yml") for p in paths)
    generated_dir = resolve_project_dir("dbt") / "models" / "generated"
    for p in paths:
        assert generated_dir in Path(p).parents
        assert f"flowt_{env['suffix']}" in Path(p).name


# ---------- 单步预览（画布上最常用的接口） ----------


def test_preview_respects_row_rules(flow_env, load_user):
    """预览拿到的行也必须过行级权限 —— 否则画布就成了越权读数的窗口。

    源表有 2 行（德清 / 安徽），owner 只有德清权限，所以预览只能看到 1 行。
    """
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        result = service.preview_node(
            s, flow, make_dsl(env), "src", pg_engine=engine, limit=10
        )

    assert result["columns"] == ["order_id", "warehouse", "amount"]
    assert len(result["rows"]) == 1
    assert result["rows"][0][1] == "德清"
    assert 'WHERE "warehouse" = \'德清\'' in result["sql"]  # SQL 里确实带了行权


def test_preview_of_downstream_node_includes_upstream(flow_env, load_user):
    """预览下游节点时要跑完整的上游链路（这里 sink 的结果应与源一致）。"""
    env, sf, engine = flow_env
    flow_id = _prepare(sf, env, load_user, make_dsl(env))

    with sf() as s:
        flow = service.get_flow(s, flow_id, load_user(env["owner"]))
        result = service.preview_node(
            s, flow, make_dsl(env), "out", pg_engine=engine, limit=10
        )
    assert len(result["rows"]) == 1
