"""向量相似度与聚类。

两件事：

1. **分段算余弦相似度** —— 不把 N×N 矩阵一次摊开（5000×5000 的 float32 就是 100MB，
   分块后峰值只有几 MB）；
2. **并查集求连通分量做分组** —— 「相似度 ≥ 阈值」的传递闭包，结果与遍历顺序无关。

为什么不用贪心质心：它按处理顺序把向量往最近的簇里塞，同一批数据换个顺序
可能分出不同的组。数据治理里最难解释的就是"为什么昨天和今天的结果不一样"。
代价是链式合并（A≈B、B≈C 但 A≉C 时三者仍同组），对"同一件商品的不同写法"
这个目标场景是合适的 —— 它们本来就围绕一个语义中心。
"""
from __future__ import annotations

STRATEGIES = ("shortest", "longest", "first")


def cosine_similarity(left, right) -> float:
    """两个向量的余弦相似度（单点比较，给测试与调用方用）。"""
    import numpy as np

    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(a @ b / (na * nb))


class _UnionFind:
    """并查集：`union(a, b)` 之后 `find(a) == find(b)`，用来求连通分量。"""

    __slots__ = ("_parent",)

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        parent = self._parent
        root = x
        while parent[root] != root:
            root = parent[root]
        # 路径压缩：把沿途节点直接挂到根上
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 小根挂到大根下，保持树浅
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb


def cluster_by_similarity(
    vectors, threshold: float, *, block_size: int = 1000
) -> list[int]:
    """按余弦相似度分组，返回每行的组号（从 0 开始且连续）。

    向量需为等长；相似度 ≥ `threshold` 的两行会被并到一组。
    """
    import numpy as np

    count = len(vectors)
    if count == 0:
        return []
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"相似度阈值应在 0~1 之间，收到 {threshold}")

    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2:
        raise ValueError("向量集合应为二维数组")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # 零向量：归一化后仍是零向量，与任何向量的点积都是 0 —— 不会误合并
    norms[norms == 0.0] = 1.0
    unit = matrix / norms

    uf = _UnionFind(count)
    step = max(1, block_size)
    for start in range(0, count, step):
        stop = min(start + step, count)
        # 只比「自己及之后」：相似度对称，算上三角就够
        scores = unit[start:stop] @ unit.T  # (本块行数, 全部行数)
        for offset in range(stop - start):
            index = start + offset
            row = scores[offset]
            hits = np.nonzero(row[index + 1 :] >= threshold)[0]
            for hit in hits:
                uf.union(index, index + 1 + int(hit))

    # 根 → 连续组号，按首次出现顺序编号（保证同输入同输出）
    labels: dict[int, int] = {}
    groups: list[int] = []
    for index in range(count):
        root = uf.find(index)
        if root not in labels:
            labels[root] = len(labels)
        groups.append(labels[root])
    return groups


def _is_better(candidate: int, current: int, texts: list[str], strategy: str) -> bool:
    if strategy == "first":
        return False
    if strategy == "shortest":
        return len(texts[candidate]) < len(texts[current])
    if strategy == "longest":
        return len(texts[candidate]) > len(texts[current])
    raise ValueError(f"未知的代表行策略：{strategy!r}，可选 {STRATEGIES}")


def pick_representatives(texts, groups, *, strategy: str = "shortest") -> list[bool]:
    """每组挑一个代表行，返回与输入等长的布尔列表。

    默认取**最短**的文本：同一个商品的多种写法里，短的通常是规范名
    （「碧根果」vs「碧根果（袋装）」），回填主数据时更该用它。
    """
    if len(texts) != len(groups):
        raise ValueError("文本数与组号数不一致")
    if strategy not in STRATEGIES:
        # 提前校验，不能等"比较真的发生了"才报错 ——
        # 否则只有一组（或每组只有一行）时，未知策略会被静默放过
        raise ValueError(f"未知的代表行策略：{strategy!r}，可选 {STRATEGIES}")
    rendered = ["" if t is None else str(t) for t in texts]

    chosen: dict[int, int] = {}
    for index, group in enumerate(groups):
        current = chosen.get(group)
        if current is None or _is_better(index, current, rendered, strategy):
            chosen[group] = index

    flags = [False] * len(texts)
    for index in chosen.values():
        flags[index] = True
    return flags


__all__ = [
    "STRATEGIES",
    "cluster_by_similarity",
    "cosine_similarity",
    "pick_representatives",
]
