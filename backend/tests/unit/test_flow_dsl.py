"""流程 DSL 的校验与拓扑排序。"""
import pytest

from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput, topological_order
from app.flow.errors import FlowValidationError


def _dsl(nodes, edges, target="tbl", **kw):
    return FlowDSL(
        name=kw.pop("name", "t"),
        nodes=nodes,
        edges=edges,
        output=FlowOutput(target_name=target),
        **kw,
    )


def _src(i):
    return FlowNode(id=i, type="source.dataset", params={"dataset_id": 1})


def _sink(i="out"):
    return FlowNode(id=i, type="sink.materialize", params={})


def _op(i, t="clean.filter", **params):
    return FlowNode(id=i, type=t, params=params or {"expr": "a > 1"})


def test_minimal_flow_is_valid():
    dsl = _dsl([_src("s"), _op("f"), _sink()], [
        FlowEdge(source="s", target="f"), FlowEdge(source="f", target="out"),
    ])
    assert dsl.sink.id == "out"
    assert dsl.source_nodes()[0].id == "s"


def test_topological_order_respects_dependencies():
    dsl = _dsl(
        [_src("s"), _op("a"), _op("b"), _sink()],
        [
            FlowEdge(source="s", target="a"),
            FlowEdge(source="a", target="b"),
            FlowEdge(source="b", target="out"),
        ],
    )
    order = [n.id for n in topological_order(dsl.nodes, dsl.edges)]
    assert order == ["s", "a", "b", "out"]


def test_rejects_two_sinks():
    with pytest.raises(FlowValidationError, match="只能有一个输出节点"):
        _dsl(
            [_src("s"), _sink("o1"), _sink("o2")],
            [FlowEdge(source="s", target="o1"), FlowEdge(source="s", target="o2")],
        )


def test_rejects_no_source():
    with pytest.raises(FlowValidationError, match="至少需要一个输入节点"):
        _dsl([_op("f"), _sink()], [FlowEdge(source="f", target="out")])


def test_rejects_cycle():
    # 环上的节点都有入边、也有出边，只有环本身能发现
    with pytest.raises(FlowValidationError, match="存在环"):
        _dsl(
            [_src("s"), _op("a"), _op("b"), _sink()],
            [
                FlowEdge(source="s", target="a"),
                FlowEdge(source="a", target="b"),
                FlowEdge(source="b", target="a"),
                FlowEdge(source="b", target="out"),
            ],
        )


def test_rejects_dangling_node():
    with pytest.raises(FlowValidationError, match="没有下游"):
        _dsl(
            [_src("s"), _op("a"), _op("orphan"), _sink()],
            [
                FlowEdge(source="s", target="a"),
                FlowEdge(source="a", target="out"),
                FlowEdge(source="s", target="orphan"),
            ],
        )


def test_rejects_orphan_node_without_input():
    with pytest.raises(FlowValidationError, match="没有输入"):
        _dsl(
            [_src("s"), _op("lonely"), _sink()],
            [FlowEdge(source="s", target="out")],
        )


def test_rejects_duplicate_node_id():
    with pytest.raises(FlowValidationError, match="节点 id 重复"):
        _dsl(
            [_src("s"), _op("f"), _op("f"), _sink()],
            [
                FlowEdge(source="s", target="f"),
                FlowEdge(source="f", target="out"),
            ],
        )


def test_rejects_edge_to_unknown_node():
    with pytest.raises(FlowValidationError, match="不存在的节点"):
        _dsl(
            [_src("s"), _sink()],
            [FlowEdge(source="s", target="ghost"), FlowEdge(source="s", target="out")],
        )


def test_rejects_self_loop():
    with pytest.raises(FlowValidationError, match="不能连接自己"):
        _dsl(
            [_src("s"), _sink()],
            [FlowEdge(source="s", target="s"), FlowEdge(source="s", target="out")],
        )


@pytest.mark.parametrize("bad", ["9tbl", "has space", "tbl-1", "表名"])
def test_rejects_illegal_target_name(bad):
    with pytest.raises(FlowValidationError, match="产物表名不合法"):
        _dsl([_src("s"), _sink()], [FlowEdge(source="s", target="out")], target=bad)


def test_rejects_node_ids_colliding_after_sanitize():
    # "a-b" 与 "a_b" 规范化后同名，编译出的 CTE 会撞，必须提前拦
    with pytest.raises(FlowValidationError, match="规范化后重复"):
        _dsl(
            [_src("s"), _op("a-b"), _op("a_b"), _sink()],
            [
                FlowEdge(source="s", target="a-b"),
                FlowEdge(source="a-b", target="a_b"),
                FlowEdge(source="a_b", target="out"),
            ],
        )
