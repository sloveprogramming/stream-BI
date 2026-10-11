"""向量算子的行为。

这里把「模型」换成**确定性假向量**，但跑的是真实代码路径：
截断 → 分批 → 维度校验 → 序列化 → 缓存 → 分组 → 代表行。只有"模型本身"是假的。

⚠️ 假向量**不代表语义模型的能力**：测试里"写法不同但指向同一商品"是靠预先指定的
向量表造出来的。真实语义效果需要用户配好模型或 API 后自行验证 ——
本文件保证的是机制正确，不是语义准确。
"""
import hashlib

import pytest

import app.ai.embedding as embedding
from app.flow.dsl import FlowNode
from app.flow.errors import FlowExecutionError, FlowValidationError
from app.flow.operators import get_operator

DIM = 64

# 精确指定的假向量：让「写法不同但指向同一商品」几乎同向，无关商品正交。
# 这样阈值行为完全可控，不依赖哈希的偶然性。
_EXACT = {
    "碧根果": [1.0, 0.02, 0.0],
    "碧根果（袋装）": [1.0, 0.0, 0.0],
    "碧根果（袋装 500g）": [0.99, 0.01, 0.0],
    "东北松子": [0.0, 0.0, 1.0],
}


def _fake_vector(text: str, dim: int = DIM) -> list[float]:
    """确定性假向量 —— **为了验证机制而构造，不代表语义模型的能力**。

    表里有的用精确值；没列的用字符 bigram 哈希兜底（只保证"同一文本 → 同一向量"）。
    真实语义效果需要用户配好模型或 API 后自行验证。
    """
    base = _EXACT.get(text)
    if base is not None:
        return (list(base) + [0.0] * dim)[:dim]

    vector = [0.0] * dim
    padded = f"^{text}$"
    for i in range(len(padded) - 1):
        gram = padded[i : i + 2]
        bucket = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16) % dim
        vector[bucket] += 1.0
    return vector


def _settings(**overrides):
    from types import SimpleNamespace

    base = {
        "embedding_provider": "api",
        "embedding_model": "fake-model",
        "embedding_api_base": "http://fake/v1",
        "embedding_api_key": "",
        "embedding_timeout_seconds": 5,
        "embedding_batch_size": 8,
        "embedding_max_chars": 1024,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_embeddings(monkeypatch):
    """把 API 那条路径的「模型」换成确定性假向量。"""
    monkeypatch.setattr(embedding, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        embedding,
        "_embed_via_api",
        lambda values, *, model, client=None: [_fake_vector(v) for v in values],
    )


def _run(op_type, params, columns, rows, session=None):
    op = get_operator(op_type)
    node = FlowNode(id="n1", type=op_type, params=params)
    return op.run_python(
        node=node, columns=list(columns), rows=[list(r) for r in rows], session=session
    )


# ---------------------------------------------------------------- embed.text


def test_embed_text_appends_one_vector_column(fake_embeddings):
    columns, rows = _run(
        "embed.text", {"input_column": "name"}, ["id", "name"], [[1, "碧根果"], [2, "松子"]]
    )
    assert columns == ["id", "name", "ai_embedding"]
    assert len(rows) == 2
    for row in rows:
        vector = embedding.parse_vector(row[2])
        assert len(vector) == DIM


def test_embed_text_vector_is_stable_for_same_text(fake_embeddings):
    """同一文本两次必须得到同一向量 —— 否则缓存与比较都失去意义。"""
    _, first = _run("embed.text", {"input_column": "name"}, ["name"], [["碧根果"]])
    _, second = _run("embed.text", {"input_column": "name"}, ["name"], [["碧根果"]])
    assert first[0][1] == second[0][1]


def test_embed_text_respects_output_column_name(fake_embeddings):
    columns, _ = _run(
        "embed.text",
        {"input_column": "name", "output_column": "vec"},
        ["name"],
        [["碧根果"]],
    )
    assert columns == ["name", "vec"]


def test_embed_text_rejects_non_ascii_output_column(fake_embeddings):
    """物理列名保持 ASCII 是本项目的硬约定。"""
    with pytest.raises(FlowValidationError, match="不合法"):
        _run("embed.text", {"input_column": "name", "output_column": "向量"}, ["name"], [["x"]])


def test_embed_text_reports_missing_input_column(fake_embeddings):
    with pytest.raises(FlowExecutionError, match="找不到输入列"):
        _run("embed.text", {"input_column": "nope"}, ["name"], [["碧根果"]])


def test_embed_text_without_provider_aborts(monkeypatch):
    """真实环境（本机就是）——没有提供方时必须中止，不能悄悄产出别的东西。"""
    monkeypatch.setattr(
        embedding, "get_settings", lambda: _settings(embedding_provider="none")
    )
    with pytest.raises(FlowExecutionError, match="向量化不可用"):
        _run("embed.text", {"input_column": "name"}, ["name"], [["碧根果"]])


# ---------------------------------------------------------------- dedup.semantic


def test_semantic_dedup_marks_groups_and_keeps_all_rows(fake_embeddings):
    texts = ["碧根果", "碧根果（袋装）", "东北松子"]
    columns, rows = _run(
        "dedup.semantic", {"input_column": "name"}, ["name"], [[t] for t in texts]
    )

    assert columns == ["name", "semantic_group", "is_representative"]
    assert len(rows) == len(texts)  # 不静默删行
    assert rows[0][1] == rows[1][1]  # 写法不同但指向同一商品 → 同组
    assert rows[0][1] != rows[2][1]  # 无关商品 → 不同组


def test_semantic_dedup_marks_exactly_one_representative_per_group(fake_embeddings):
    texts = ["碧根果", "碧根果（袋装）", "东北松子"]
    _, rows = _run(
        "dedup.semantic", {"input_column": "name"}, ["name"], [[t] for t in texts]
    )
    counts: dict[int, int] = {}
    for row in rows:
        if row[2]:
            counts[row[1]] = counts.get(row[1], 0) + 1
    assert set(counts.values()) == {1}  # 每组恰好一个代表
    assert len(counts) == 2  # 两组


def test_semantic_dedup_representative_is_the_shortest(fake_embeddings):
    texts = ["碧根果（袋装 500g）", "碧根果"]
    _, rows = _run(
        "dedup.semantic", {"input_column": "name"}, ["name"], [[t] for t in texts]
    )
    assert rows[0][1] == rows[1][1]
    assert [row[2] for row in rows] == [False, True]


def test_semantic_dedup_high_threshold_keeps_them_apart(fake_embeddings):
    """阈值是硬约束：把阈值抬到几乎要求完全同向时，相似的写法也不该被合并。"""
    texts = ["碧根果", "碧根果（袋装）"]
    _, rows = _run(
        "dedup.semantic",
        {"input_column": "name", "threshold": 0.9999},
        ["name"],
        [[t] for t in texts],
    )
    assert rows[0][1] != rows[1][1]


def test_semantic_dedup_can_emit_vectors(fake_embeddings):
    columns, rows = _run(
        "dedup.semantic",
        {"input_column": "name", "emit_vector": True},
        ["name"],
        [["碧根果"]],
    )
    assert columns == ["name", "semantic_group", "is_representative", "ai_embedding"]
    assert len(embedding.parse_vector(rows[0][3])) == DIM


def test_semantic_dedup_rejects_unknown_representative_strategy(fake_embeddings):
    with pytest.raises(FlowValidationError, match="代表行策略"):
        _run(
            "dedup.semantic",
            {"input_column": "name", "representative": "random"},
            ["name"],
            [["碧根果"]],
        )


def test_semantic_dedup_keeps_row_order(fake_embeddings):
    """分组信息是附加列，不能改变行的顺序 —— 下游还要靠顺序对齐原始记录。"""
    texts = ["东北松子", "碧根果", "东北松子"]
    _, rows = _run(
        "dedup.semantic", {"input_column": "name"}, ["name"], [[t] for t in texts]
    )
    assert [row[0] for row in rows] == texts


def test_semantic_dedup_shares_cache_with_embed_text(
    fake_embeddings, pg_session_factory, monkeypatch
):
    """先跑过 embed.text 的文本，在 dedup.semantic 里不该重新算向量。"""
    calls: list[list[str]] = []
    inner = embedding._embed_via_api

    def counting(values, *, model, client=None):
        calls.append(list(values))
        return inner(values, model=model, client=client)

    monkeypatch.setattr(embedding, "_embed_via_api", counting)

    texts = ["碧根果", "东北松子"]
    try:
        with pg_session_factory() as session:
            _run(
                "embed.text", {"input_column": "name"}, ["name"],
                [[t] for t in texts], session,
            )
            assert calls, "第一次必须真的算了向量"
            calls.clear()
            _run(
                "dedup.semantic", {"input_column": "name"}, ["name"],
                [[t] for t in texts], session,
            )
            assert calls == [], "第二次应当命中缓存，不再调用模型"
    finally:
        # 这组缓存键只属于本测试（模型名是假的），清掉不污染本地库
        from app.models import AiLlmCache

        with pg_session_factory() as session:
            session.query(AiLlmCache).filter(
                AiLlmCache.cache_key.like("embed.text|%fake-model")
            ).delete(synchronize_session=False)
            session.commit()
