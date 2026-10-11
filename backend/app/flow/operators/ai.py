"""AI 算子：把 LLM 能力做成画布上的一个节点，和「过滤」「JOIN」直接连线。

这类算子**不产 SQL** —— dbt 只会写 SQL，调不了模型。所以它们的执行方式是：

    执行器编译「到本节点为止」的 SELECT → 取数（已套行级权限）
      → 算子把这批文本喂给模型 → 返回要物化的 (列名, 行)
      → 执行器物化成表，下游 dbt 模型 JOIN 它

于是「非结构化文本处理」和「结构化 SQL 加工」能在同一条管道里协作 ——
这是普通 ETL 平台做不到的部分（它们处理不了文本与语义判断）。

成本三控都在 `app/ai/operators.run_llm_batch`：去重、缓存、分批并发。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.access.scoped_query import check_ident
from app.ai.client import structured_call
from app.ai.errors import AiError
from app.ai.operators import run_llm_batch
from app.ai.similarity import STRATEGIES, cluster_by_similarity, pick_representatives
from app.flow.errors import FlowExecutionError, FlowValidationError
from app.flow.operators.base import Operator, register
from app.settings import get_settings

DEFAULT_BATCH_SIZE = 20
DEFAULT_CONCURRENCY = 4


class _IndexedLabel(BaseModel):
    index: int = Field(description="对应输入列表里的序号")
    label: str


class _LabelResult(BaseModel):
    results: list[_IndexedLabel]


class _IndexedFields(BaseModel):
    index: int
    fields: dict[str, str | None]


class _ExtractResult(BaseModel):
    results: list[_IndexedFields]


def _numbered(values: list[str]) -> str:
    return "\n".join(f"[{i}] {v}" for i, v in enumerate(values))


def _call_classify(
    values: list[str], *, labels: list[str], instructions: str, criteria: str = ""
) -> dict[str, Any]:
    """一次批量分类调用。单独抽出函数是为了测试时能打桩，不碰网络。"""
    rules = []
    if instructions:
        rules.append(f"额外要求：{instructions}")
    if criteria:
        rules.append(f"判定标准：{criteria}")
    prompt = (
        f"把下面每条文本归到这些标签之一：{', '.join(labels)}。\n"
        + ("\n".join(rules) + "\n" if rules else "")
        + "逐条输出 index（输入里的序号，从 0 开始）与 label（必须是给定标签之一）。\n\n"
        + _numbered(values)
    )
    result = structured_call(prompt, _LabelResult)
    valid = set(labels)
    out: dict[str, Any] = {}
    for item in result.results:
        if 0 <= item.index < len(values):
            out[values[item.index]] = item.label if item.label in valid else None
    return out


def _call_extract(
    values: list[str], *, fields: dict[str, str]
) -> dict[str, Any]:
    """一次批量字段抽取调用。"""
    field_desc = "\n".join(f"- {name}：{desc}" for name, desc in fields.items())
    prompt = (
        "从下面每条文本里抽取指定字段，抽不到的填 null，不要编造。\n"
        f"要抽取的字段：\n{field_desc}\n\n"
        "逐条输出 index（输入里的序号，从 0 开始）与 fields（键必须是上面列出的字段名）。\n\n"
        + _numbered(values)
    )
    result = structured_call(prompt, _ExtractResult)
    out: dict[str, Any] = {}
    for item in result.results:
        if 0 <= item.index < len(values):
            out[values[item.index]] = {k: item.fields.get(k) for k in fields}
    return out


def _require_column(columns: list[str], name: str, op_type: str) -> int:
    if name not in columns:
        raise FlowExecutionError(
            f"算子 {op_type} 找不到输入列 {name!r}；上游实际列：{columns}"
        )
    return columns.index(name)


def _check_output_column(name: str, op_type: str) -> None:
    try:
        check_ident(name)
    except ValueError as exc:
        raise FlowValidationError(
            f"算子 {op_type} 的输出列名 {name!r} 不合法（物理列名只允许字母/数字/下划线）。"
        ) from exc


def _wrap_ai_errors(op_type: str, fn, *args, **kwargs):
    """把 AI 层异常翻译成编排层异常，调用方（执行器）只需要认后者。"""
    try:
        return fn(*args, **kwargs)
    except AiError as exc:
        raise FlowExecutionError(f"算子 {op_type} 调用模型失败：{exc}") from exc


class LlmClassifyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_column: str = Field(description="要做分类的文本列")
    labels: list[str] = Field(min_length=2, description="候选标签（模型只能选其中之一）")
    instructions: str = Field(default="", description="额外要求，如「按商品主类目归类，忽略包装规格」")
    output_column: str = Field(default="ai_label", description="结果写进哪个新列")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=100, description="每次调用模型处理多少条")
    max_concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=16, description="同时发起几个请求")


@register
class LlmClassify(Operator):
    type = "llm.classify"
    category = "AI"
    title = "智能分类打标"
    description = "把一列文本按给定标签分类（如 商品标题 → 类目，可解决 ERP 分类编码与业务口径对不上的问题）"
    Params = LlmClassifyParams
    engine = "python"

    def run_python(self, *, node, columns, rows, session=None):
        p = self.parse(node)
        _check_output_column(p.output_column, self.type)
        idx = _require_column(columns, p.input_column, self.type)

        values = [None if r[idx] is None else str(r[idx]) for r in rows]
        mapping = _wrap_ai_errors(
            self.type,
            run_llm_batch,
            values=values,
            # 缓存键必须包含所有影响结果的参数，否则改了标签却复用旧标签
            cache_key=f"{self.type}|{'/'.join(p.labels)}|{p.instructions}",
            per_batch=lambda batch: _call_classify(
                batch, labels=p.labels, instructions=p.instructions
            ),
            batch_size=p.batch_size,
            max_concurrency=p.max_concurrency,
            session=session,
        )
        out_rows = [[*r, mapping.get(values[i])] for i, r in enumerate(rows)]
        return [*columns, p.output_column], out_rows


class LlmJudgeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_column: str = Field(description="要判定的文本列")
    criteria: str = Field(description="判定标准，如「是否疑似串货：收货仓与订单仓不一致」")
    labels: list[str] = Field(default=["是", "否"], min_length=2, description="判定结论标签")
    output_column: str = Field(default="ai_verdict")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=100)
    max_concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=16)


@register
class LlmJudge(Operator):
    type = "llm.judge"
    category = "AI"
    title = "智能判定"
    description = "按给定标准对每条记录下结论（如 订单是否异常、评论是否差评）"
    Params = LlmJudgeParams
    engine = "python"

    def run_python(self, *, node, columns, rows, session=None):
        p = self.parse(node)
        _check_output_column(p.output_column, self.type)
        idx = _require_column(columns, p.input_column, self.type)

        values = [None if r[idx] is None else str(r[idx]) for r in rows]
        mapping = _wrap_ai_errors(
            self.type,
            run_llm_batch,
            values=values,
            cache_key=f"{self.type}|{'/'.join(p.labels)}|{p.criteria}",
            per_batch=lambda batch: _call_classify(
                batch, labels=p.labels, instructions="", criteria=p.criteria
            ),
            batch_size=p.batch_size,
            max_concurrency=p.max_concurrency,
            session=session,
        )
        out_rows = [[*r, mapping.get(values[i])] for i, r in enumerate(rows)]
        return [*columns, p.output_column], out_rows


class LlmExtractParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_column: str = Field(description="要抽取信息的文本列")
    fields: dict[str, str] = Field(
        min_length=1, description="字段名 → 说明，如 {'city': '收货城市', 'contact': '联系人'}"
    )
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=100)
    max_concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=16)


@register
class LlmExtract(Operator):
    type = "llm.extract"
    category = "AI"
    title = "智能字段抽取"
    description = "从非结构化文本里抽字段（如 从客服聊天记录抽 问题类型/紧急度）"
    Params = LlmExtractParams
    engine = "python"

    def run_python(self, *, node, columns, rows, session=None):
        p = self.parse(node)
        for name in p.fields:
            _check_output_column(name, self.type)
        idx = _require_column(columns, p.input_column, self.type)

        values = [None if r[idx] is None else str(r[idx]) for r in rows]
        mapping = _wrap_ai_errors(
            self.type,
            run_llm_batch,
            values=values,
            cache_key=f"{self.type}|{sorted(p.fields.items())}",
            per_batch=lambda batch: _call_extract(batch, fields=p.fields),
            batch_size=p.batch_size,
            max_concurrency=p.max_concurrency,
            session=session,
        )
        names = list(p.fields)
        out_rows = []
        for i, r in enumerate(rows):
            extracted = mapping.get(values[i]) or {}
            out_rows.append([*r, *[extracted.get(n) for n in names]])
        return [*columns, *names], out_rows


# ---------------------------------------------------------------- 向量算子
#
# 与上面三个 LLM 算子的区别：它们靠「提示词 + 结构化输出」，这两个靠**向量 + 数值计算**。
# 提供方因此不是 LLM 而是 embedding 模型（本地或 HTTP API），
# 而「环境没准备好」必须是一次**可操作的失败**（见 app/ai/embedding.py 的说明）。
#
# 缓存与并发沿用同一条管道（`run_llm_batch`）—— 向量也是「文本 → 结果」，
# 去重、跨运行缓存、分批并发一样适用。


def embedding_fingerprint() -> str:
    """缓存键里代表「用哪套坐标」的那一段。

    必须含提供方与模型：换模型后复用旧向量，等于拿两套不同坐标系的距离去比 ——
    不会报错，只会默默给出错的分组。
    """
    from app.ai.embedding import resolve_status

    status = resolve_status()
    return f"{status.provider}|{status.model}"


def run_embedding_batch(*, values: list[str], session=None) -> dict[str, str]:
    """文本 → 序列化后的向量。走与 LLM 算子同一条成本管道。"""
    from app.ai.embedding import embed_texts, serialize_vector

    def per_batch(batch: list[str]) -> dict[str, Any]:
        outcome = embed_texts(batch)
        return {
            text: serialize_vector(vector)
            for text, vector in zip(batch, outcome.vectors)
        }

    return run_llm_batch(
        values=values,
        # 与 embed.text 共用同一个 key：先跑过向量化的流程，这里直接命中缓存
        cache_key=f"embed.text|{embedding_fingerprint()}",
        per_batch=per_batch,
        batch_size=get_settings().embedding_batch_size,
        model=embedding_fingerprint(),
        session=session,
    )


def _ordered_vectors(
    texts: list[str], serialized: dict[str, str], op_type: str
) -> list[list[float]]:
    """按输入顺序取回向量。**缺一条就中止** —— 向量不齐没法比较，不能拿空的顶替。"""
    from app.ai.embedding import parse_vector

    raw_values = [serialized.get(text) for text in texts]
    missing = [text for text, raw in zip(texts, raw_values) if not raw]
    if missing:
        raise FlowExecutionError(
            f"算子 {op_type} 有 {len(missing)} 条文本没拿到向量"
            f"（例如 {missing[0][:40]!r}）。向量不齐无法比较，拒绝继续。"
        )
    return [parse_vector(raw) for raw in raw_values]


class EmbedTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_column: str = Field(description="要向量化的文本列")
    output_column: str = Field(default="ai_embedding", description="向量写进哪个新列（JSON 文本）")


@register
class EmbedText(Operator):
    type = "embed.text"
    category = "AI"
    title = "文本向量化"
    description = (
        "把一列文本转成向量（以 JSON 文本落列）。向量供语义去重与相似检索使用；"
        "运行前先用 python scripts/check_embedding.py 确认向量能力已就绪"
    )
    Params = EmbedTextParams
    engine = "python"

    def run_python(self, *, node, columns, rows, session=None):
        p = self.parse(node)
        _check_output_column(p.output_column, self.type)
        index = _require_column(columns, p.input_column, self.type)

        values = [None if row[index] is None else str(row[index]) for row in rows]
        mapping = _wrap_ai_errors(
            self.type, run_embedding_batch, values=values, session=session
        )
        out_rows = [[*row, mapping.get(values[i])] for i, row in enumerate(rows)]
        return [*columns, p.output_column], out_rows


class SemanticDedupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_column: str = Field(description="要比对的文本列")
    threshold: float = Field(
        default=0.92,
        ge=0.5,
        le=1.0,
        description="判定为同一组的余弦相似度阈值：越高越严格（漏合并优于误合并）",
    )
    group_column: str = Field(default="semantic_group", description="组号写进哪个新列")
    flag_column: str = Field(
        default="is_representative", description="代表行标记写进哪个新列"
    )
    representative: str = Field(
        default="shortest",
        description="组内代表行怎么挑：shortest（默认，取最短文本，通常是规范名）/ longest / first",
    )
    emit_vector: bool = Field(
        default=False, description="是否把向量也落一列（调试用，约 6KB/行）"
    )


@register
class SemanticDedup(Operator):
    type = "dedup.semantic"
    category = "AI"
    title = "语义去重（标记分组）"
    description = (
        "按语义相似度把文本分组，例如「碧根果」与「碧根果（袋装）」判为同一商品。"
        "只加分组列、不删行；要只留代表行，后面接一个「过滤」算子即可。"
        "需要先准备向量能力（python scripts/check_embedding.py）"
    )
    Params = SemanticDedupParams
    engine = "python"

    def run_python(self, *, node, columns, rows, session=None):
        p = self.parse(node)
        _check_output_column(p.group_column, self.type)
        _check_output_column(p.flag_column, self.type)
        if p.emit_vector:
            _check_output_column("ai_embedding", self.type)
        if p.representative not in STRATEGIES:
            raise FlowValidationError(
                f"算子 {self.type} 的代表行策略 {p.representative!r} 不合法，可选 {STRATEGIES}"
            )
        index = _require_column(columns, p.input_column, self.type)

        texts = ["" if row[index] is None else str(row[index]) for row in rows]
        serialized = _wrap_ai_errors(
            self.type, run_embedding_batch, values=texts, session=session
        )
        vectors = _ordered_vectors(texts, serialized, self.type)

        groups = _wrap_ai_errors(
            self.type, cluster_by_similarity, vectors, p.threshold
        )
        flags = _wrap_ai_errors(
            self.type,
            pick_representatives,
            texts,
            groups,
            strategy=p.representative,
        )

        out_rows = []
        for i, row in enumerate(rows):
            extra: list[Any] = [groups[i], flags[i]]
            if p.emit_vector:
                extra.append(serialized.get(texts[i]))
            out_rows.append([*row, *extra])

        header = [*columns, p.group_column, p.flag_column]
        if p.emit_vector:
            header.append("ai_embedding")
        return header, out_rows
