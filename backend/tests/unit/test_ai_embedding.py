"""向量能力的提供方选择、拒绝路径与相似度计算。

本文件最重要的一组断言是**拒绝**：本机既没装本地依赖、也没配 API，
所以"不可用"不是假设而是当前事实 —— 这几条用例在真实环境里跑的就是那条分支。

另一半是相似度与聚类：它们不依赖任何模型，可以彻底确定性地验证。
"""
from types import SimpleNamespace

import pytest

import app.ai.embedding as embedding
from app.ai.errors import AiError, AiUnavailableError
from app.ai.similarity import (
    cluster_by_similarity,
    cosine_similarity,
    pick_representatives,
)


def _settings(**overrides):
    base = {
        "embedding_provider": "auto",
        "embedding_model": "test-model",
        "embedding_api_base": "",
        "embedding_api_key": "",
        "embedding_timeout_seconds": 10,
        "embedding_batch_size": 8,
        "embedding_max_chars": 1024,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def use_settings(monkeypatch):
    def _apply(**overrides):
        monkeypatch.setattr(embedding, "get_settings", lambda: _settings(**overrides))

    return _apply


# ---------------------------------------------------------------- 提供方选择


def test_auto_prefers_api_when_configured(use_settings):
    use_settings(embedding_provider="auto", embedding_api_base="http://x/v1")
    status = embedding.resolve_status()
    assert status.provider == "api"
    assert status.usable is True


def test_auto_uses_local_when_no_api_but_local_ready(use_settings, monkeypatch):
    use_settings(embedding_provider="auto")
    monkeypatch.setattr(embedding, "local_available", lambda: (True, ""))
    status = embedding.resolve_status()
    assert status.provider == "local"
    assert status.usable is True


def test_auto_reports_none_when_nothing_is_available(use_settings, monkeypatch):
    use_settings(embedding_provider="auto")
    monkeypatch.setattr(embedding, "local_available", lambda: (False, "未安装 FlagEmbedding"))
    status = embedding.resolve_status()
    assert status.provider == "none"
    assert status.usable is False
    assert "FlagEmbedding" in status.reason  # 理由要说清缺什么
    assert "BISQL_EMBEDDING_API_BASE" in status.fix_hint  # 出路要说清怎么改


def test_explicit_local_never_falls_back_to_api(use_settings, monkeypatch):
    """用户说用本地，就不能"体贴地"替他改成 API —— 数据出不出机器是他的决定。"""
    use_settings(embedding_provider="local", embedding_api_base="http://x/v1")
    monkeypatch.setattr(embedding, "local_available", lambda: (False, "未安装 FlagEmbedding"))
    status = embedding.resolve_status()
    assert status.provider == "none"
    assert status.usable is False


def test_explicit_api_without_base_url_is_rejected(use_settings):
    use_settings(embedding_provider="api", embedding_api_base="")
    status = embedding.resolve_status()
    assert status.provider == "none"
    assert "BISQL_EMBEDDING_API_BASE" in status.reason


def test_explicit_none_disables_the_capability(use_settings):
    use_settings(embedding_provider="none", embedding_api_base="http://x/v1")
    status = embedding.resolve_status()
    assert status.usable is False
    assert "none" in status.reason


def test_embed_raises_actionable_error_when_unavailable(use_settings, monkeypatch):
    """错误信息必须同时给出两条出路，否则用户只知道"不能用"却不知怎么办。"""
    use_settings(embedding_provider="auto")
    monkeypatch.setattr(embedding, "local_available", lambda: (False, "未安装 FlagEmbedding"))
    with pytest.raises(AiUnavailableError) as excinfo:
        embedding.embed_texts(["随便一段文本"])
    message = str(excinfo.value)
    assert "BISQL_EMBEDDING_API_BASE" in message
    assert "FlagEmbedding" in message


def test_empty_input_does_not_touch_the_provider(use_settings, monkeypatch):
    """空输入直接返回 —— 不该因为"没有向量能力"而让一次空跑失败。"""
    use_settings(embedding_provider="none")
    monkeypatch.setattr(
        embedding, "resolve_status", lambda: pytest.fail("空输入不应触发提供方探测")
    )
    outcome = embedding.embed_texts([])
    assert outcome.vectors == []


# ---------------------------------------------------------------- API 路径


class _FakeEmbeddings:
    def __init__(self):
        self.calls = []

    def create(self, *, model, input):
        self.calls.append((model, list(input)))
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(len(t)), 1.0]) for t in input]
        )


class _FakeClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddings()


def test_api_path_batches_requests(use_settings):
    use_settings(embedding_provider="api", embedding_api_base="http://x/v1", embedding_batch_size=2)
    client = _FakeClient()
    outcome = embedding.embed_texts(["a", "b", "c", "d", "e"], client=client)

    assert [len(chunk) for _, chunk in client.embeddings.calls] == [2, 2, 1]
    assert outcome.provider == "api"
    assert outcome.dim == 2
    assert len(outcome.vectors) == 5


def test_api_path_rejects_inconsistent_dimensions(use_settings):
    """维度不一致必须报错：下游按定长向量算距离，混维度只会静默出错。"""
    use_settings(embedding_provider="api", embedding_api_base="http://x/v1")

    class _RaggedClient:
        def __init__(self):
            self.embeddings = SimpleNamespace(
                create=lambda *, model, input: SimpleNamespace(
                    data=[
                        SimpleNamespace(embedding=[1.0] * (2 + i))
                        for i, _ in enumerate(input)
                    ]
                )
            )

    with pytest.raises(AiError, match="维度不一致"):
        embedding.embed_texts(["a", "b"], client=_RaggedClient())


# ---------------------------------------------------------------- 向量表示


def test_vector_roundtrip_keeps_length(use_settings):
    use_settings()
    values = [0.123456789, -1.5, 0.0, 1e-7]
    restored = embedding.parse_vector(embedding.serialize_vector(values))
    assert len(restored) == len(values)
    assert restored[0] == pytest.approx(0.123457, abs=1e-6)


def test_serialize_keeps_precision_for_comparison():
    """保留 6 位小数是为了省体积，但不能粗到把本来可比的两个数抹平。"""
    a = embedding.serialize_vector([0.1234567])
    b = embedding.serialize_vector([0.1234561])
    assert a != b


def test_parse_vector_rejects_garbage():
    with pytest.raises(AiError, match="合法 JSON"):
        embedding.parse_vector("not-json")


def test_truncate_respects_the_limit(use_settings):
    use_settings(embedding_max_chars=4)
    assert embedding.truncate_text("一二三四五六") == "一二三四"
    assert embedding.truncate_text(None) == ""


# ---------------------------------------------------------------- 相似度


def test_cosine_of_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_with_zero_vector_is_zero():
    """零向量不该被判成"和谁都像" —— 那会把一批空文本合并成一组。"""
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------- 聚类


def test_cluster_merges_similar_vectors():
    groups = cluster_by_similarity([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]], 0.95)
    assert groups[0] == groups[1]
    assert groups[0] != groups[2]


def _partition_key(labels):
    """把组号规范化。

    组号是按「首次出现顺序」编的，打乱输入后编号自然会变 ——
    要比较的是**划分结构**（谁和谁同组），不是编号本身。
    """
    mapping: dict[int, int] = {}
    out = []
    for label in labels:
        if label not in mapping:
            mapping[label] = len(mapping)
        out.append(mapping[label])
    return out


def test_cluster_is_independent_of_order():
    """顺序无关是选并查集而不是贪心质心的理由 —— 划分结果必须可复现。"""
    vectors = [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0], [0.02, 0.99]]
    forward = cluster_by_similarity(vectors, 0.95)

    order = [2, 0, 3, 1]
    shuffled = cluster_by_similarity([vectors[i] for i in order], 0.95)

    # 把打乱后的分组还原回原顺序，划分结构应当与正序完全一致
    restored = [None] * len(vectors)
    for position, original in enumerate(order):
        restored[original] = shuffled[position]
    assert _partition_key(restored) == _partition_key(forward)


def test_cluster_labels_are_contiguous_from_zero():
    groups = cluster_by_similarity([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], 0.99)
    assert sorted(set(groups)) == [0, 1]


def test_cluster_handles_empty_input():
    assert cluster_by_similarity([], 0.9) == []


def test_cluster_rejects_out_of_range_threshold():
    with pytest.raises(ValueError, match="阈值"):
        cluster_by_similarity([[1.0, 0.0]], 1.5)


def test_cluster_does_not_merge_zero_vectors(use_settings):
    """空文本 → 零向量。它们之间不该因为"都一样"而被合并成一组。"""
    groups = cluster_by_similarity([[0.0, 0.0], [0.0, 0.0], [1.0, 0.0]], 0.9)
    assert groups[0] != groups[1]


# ---------------------------------------------------------------- 代表行


def test_representative_prefers_shortest_text():
    texts = ["碧根果（袋装 500g）", "碧根果", "碧根果大包装"]
    flags = pick_representatives(texts, [0, 0, 0], strategy="shortest")
    assert flags == [False, True, False]


def test_representative_first_keeps_earliest():
    texts = ["碧根果（袋装 500g）", "碧根果"]
    flags = pick_representatives(texts, [0, 0], strategy="first")
    assert flags == [True, False]


def test_every_group_gets_exactly_one_representative():
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    groups = [0, 0, 1, 1, 1]
    flags = pick_representatives(texts, groups)
    assert sum(flags) == 2
    assert flags[0] and flags[2]  # 各自组内最短


def test_representative_rejects_unknown_strategy():
    with pytest.raises(ValueError, match="未知的代表行策略"):
        pick_representatives(["a"], [0], strategy="random")


def test_representative_requires_matching_lengths():
    with pytest.raises(ValueError, match="不一致"):
        pick_representatives(["a", "b"], [0])
