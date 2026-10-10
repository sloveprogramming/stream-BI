"""指标字典：口径的唯一来源。

**一个来源、多个消费方**（与权限裁剪器同一个思路）：
编排编译器（`flow/compile.py` 展开指标为公式）与问数提示词（`assistant/prompts.py`
注入口径）都从这里取 —— 两处各解析一次，迟早会出现"编排用旧口径、问数用新口径"。

定义文件放在 dbt 项目里（`dbt/models/marts/metrics.yml`），随代码版本化：
改指标 = 一次可评审的代码变更，"这个数该问谁"写在 `owner` 里。

## 为什么不用 MetricFlow 语义模型

立项时打算用 dbt 的语义模型格式承载。**实测后改掉了**（`dbt parse` 能加载，不是跑不通）：

1. **层次不匹配**。MetricFlow 的 metric 描述的是**聚合度量**（`sum(amount)`），
   而排编的 `field.derive` 需要**行级表达式**（`amount - discount`）——
   前者内联进 `SELECT *, (...) AS col` 是非法的（PostgreSQL 会报
   aggregate-without-groupby）。这是语义冲突，不是偏好问题；
2. **字段不是一等公民**。要的 `synonyms`（同义词）与 `owner`（负责人）
   MetricFlow 不原生支持，只能塞进 `meta` —— 那已经是"自定义格式寄生在标准格式里"；
3. **样板成本**。加一个指标还要配 time spine + entity + dimension + measure，
   对本项目（单人维护、口径就是一条 SQL 表达式）是纯负担。

代价是 dbt 不再校验这个文件；换来的是加载即校验（见 `load_dictionary`），
而且校验比 dbt 的 schema 校验更严（会去查列到底存不存在）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import sqlglot
import yaml

from app.metrics.errors import (
    MetricNotFoundError,
    MetricUnavailableError,
    MetricValidationError,
)

# 默认定义文件（相对 backend/）。允许调用方覆盖，便于测试与多环境。
DEFAULT_RELATIVE_PATH = "dbt/models/marts/metrics.yml"
_DOC_KEY = "metrics_dictionary"


# ---------------------------------------------------------------- 公式解析


def extract_columns(expr: str) -> tuple[str, ...]:
    """从公式里抽出它引用的列名。

    用 sqlglot 而不是正则：`(amount - discount) / nullif(amount, 0)` 这种
    表达式里，`nullif`、`0`、运算符都不是列名，正则必然误判。
    这是「引用的列必须存在」这条校验的基石。

    表限定名（`t.amount`）按裸列名 `amount` 处理 —— 指标公式作用在单表上，
    写表前缀属于多余，但不至于因此拒绝。
    """
    try:
        tree = sqlglot.parse_one(f"SELECT {expr}", read="postgres")
    except Exception as exc:  # noqa: BLE001 —— sqlglot 的异常类型不稳定
        raise MetricValidationError(
            f"公式不是合法的 SQL 表达式：{expr!r}（{type(exc).__name__}: {exc}）"
        ) from exc
    columns: list[str] = []
    for col in tree.find_all(sqlglot.exp.Column):
        name = col.name
        if name and name not in columns:
            columns.append(name)
    if not columns:
        raise MetricValidationError(
            f"公式 {expr!r} 没有引用任何列 —— 口径必须是能被计算的表达式，"
            f"不能是常量。"
        )
    return tuple(columns)


# ---------------------------------------------------------------- 不可变结构


@dataclass(frozen=True, slots=True)
class Metric:
    """一个指标的口径定义。**不可变** —— 加载后不允许被就地修改。

    这是一份"契约"：编排展开它、问数引用它，谁都不该改它。
    """

    name: str  # 机器名（ASCII），编排 DSL 里引用的就是它
    label: str  # 展示名，中文
    expr: str  # 计算口径（行级 SQL 表达式）
    dataset: str  # 锚定的数据集名
    owner: str  # 负责人 —— "这个数不对该问谁"
    synonyms: tuple[str, ...] = ()
    grain: tuple[str, ...] = ()
    unit: str = ""
    description: str = ""

    def aliases(self) -> tuple[str, ...]:
        """可被检索到的词：名称、展示名、同义词（去重且保序）。"""
        return tuple(dict.fromkeys([self.name, self.label, *self.synonyms]))

    @property
    def columns(self) -> tuple[str, ...]:
        """公式引用的列（加载时已校验过存在性）。"""
        return extract_columns(self.expr)


@dataclass(frozen=True, slots=True)
class MetricDictionary:
    """指标字典。整体不可变：`metrics` 是 tuple，没有可变字段。"""

    metrics: tuple[Metric, ...] = ()
    # 锚定数据集尚未上架的指标名：它们仍在字典里（问数能看到），但被引用时拒绝
    unavailable: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()
    path: str = ""

    def __bool__(self) -> bool:
        return bool(self.metrics)

    def __len__(self) -> int:
        return len(self.metrics)

    def names(self) -> tuple[str, ...]:
        return tuple(m.name for m in self.metrics)

    def get(self, name: str) -> Metric:
        """按机器名取指标。找不到时**必须列出可用清单**，否则排错只能去翻文件。"""
        for metric in self.metrics:
            if metric.name == name:
                if name in self.unavailable:
                    raise MetricUnavailableError(
                        f"指标「{metric.label}」({name}) 锚定的数据集 "
                        f"「{metric.dataset}」在当前环境尚未上架，因此不可用。"
                        f"请先注册该数据集（开发环境可跑 "
                        f"python scripts/seed_metrics_demo.py），"
                        f"或修改指标定义里的 dataset。"
                    )
                return metric
        available = ", ".join(self.names()) or "（字典为空）"
        raise MetricNotFoundError(f"未知指标 {name!r}。可用指标：{available}")

    def search(self, text: str) -> list[Metric]:
        """按名称 / 展示名 / 同义词检索。精确命中排在子串命中前面。

        支持子串是为了「毛利」能命中「毛利率」—— 业务方不会记得全称。
        """
        query = (text or "").strip().casefold()
        if not query:
            return []
        exact: list[Metric] = []
        partial: list[Metric] = []
        for metric in self.metrics:
            aliases = [a.casefold() for a in metric.aliases() if a]
            if query in aliases:
                exact.append(metric)
            elif any(query in a or a in query for a in aliases):
                partial.append(metric)
        return exact + partial

    def render(self) -> str:
        """渲染成给大模型看的口径清单。空字典返回空串（不污染 prompt）。"""
        usable = [m for m in self.metrics if m.name not in self.unavailable]
        if not usable:
            return ""
        lines = [
            "指标口径词典（这是唯一口径来源，必须照此生成 SQL；",
            "用户提到的业务词若命中「也叫」，就用对应指标的口径，不要自行简化或另立算法）：",
        ]
        for metric in usable:
            parts = [f"{metric.label}（{metric.name}）"]
            others = [s for s in metric.synonyms]
            if others:
                parts.append(f"也叫：{'、'.join(others)}")
            parts.append(f"数据集：{metric.dataset}")
            parts.append(f"口径：{metric.expr}")
            if metric.unit:
                parts.append(f"单位：{metric.unit}")
            parts.append(f"负责人：{metric.owner}")
            lines.append("- " + "｜".join(parts))
        return "\n".join(lines)


# ---------------------------------------------------------------- 加载与校验


def default_path() -> Path:
    """默认定义文件路径。定位到 `backend/dbt/models/marts/metrics.yml`。

    不用 `settings` 读是为了让本模块不依赖配置，便于单测直接传临时文件。
    需要换路径的环境（多套指标定义）用 `load_dictionary(path=...)` 显式传。
    """
    # app/metrics/loader.py -> app/metrics -> app -> backend
    return Path(__file__).resolve().parents[2] / DEFAULT_RELATIVE_PATH


def load_dictionary(
    path: str | Path | None = None,
    *,
    datasets: Mapping[str, Sequence[str]] | None = None,
) -> MetricDictionary:
    """加载并校验指标字典。

    `datasets` 是「数据集名 → 列清单」的映射。给定时做**语义校验**：

    - 指标引用了数据集里不存在的列 → 抛 `MetricValidationError`
      （这是定义写错了，越早暴露越好）；
    - 指标锚定的数据集本身还没上架 → 不抛错，把该指标标记为 `unavailable`
      并给出告警。数据集是本项目**运行时上架**的，让代码仓库强制
      "某个数据集必须已存在"会把指标字典变成一堆部署顺序陷阱；
      真正被引用时再由 `get()` 拒绝，错误信息也更准。

    不给 `datasets` 时只做**结构校验**（名称唯一、公式非空、负责人非空）。
    生产链路的两个消费方都会带上 `datasets`，所以完整校验必然生效。
    """
    target = Path(path) if path is not None else default_path()
    raw = _read_yaml(target)
    entries = raw.get(_DOC_KEY)
    if entries is None:
        raise MetricValidationError(
            f"指标文件 {target} 缺少顶层键 {_DOC_KEY!r}。"
            f"（这个键 dbt 不认识，只有本项目会读它；见文件内注释。）"
        )
    if not isinstance(entries, list):
        raise MetricValidationError(f"{_DOC_KEY} 必须是列表，实际是 {type(entries).__name__}")

    metrics: list[Metric] = []
    warnings: list[str] = []
    unavailable: set[str] = set()
    seen: dict[str, int] = {}

    for index, entry in enumerate(entries, start=1):
        where = f"{target.name} 第 {index} 条"
        if not isinstance(entry, dict):
            raise MetricValidationError(f"{where} 不是键值结构")
        metric = _parse_metric(entry, where)
        if metric.name in seen:
            raise MetricValidationError(
                f"指标名重复：{metric.name!r}"
                f"（第 {seen[metric.name]} 条与第 {index} 条）。"
                f"名称是编排引用与审计追溯的键，必须唯一。"
            )
        seen[metric.name] = index

        if datasets is not None:
            note, is_unavailable = _check_against_datasets(metric, datasets)
            if is_unavailable:
                unavailable.add(metric.name)
            if note:
                warnings.append(note)

        metrics.append(metric)

    return MetricDictionary(
        metrics=tuple(metrics),
        unavailable=frozenset(unavailable),
        warnings=tuple(warnings),
        path=str(target),
    )


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise MetricValidationError(
            f"指标定义文件不存在：{path}。"
            f"请确认已在 dbt 项目里创建 {DEFAULT_RELATIVE_PATH}。"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MetricValidationError(f"指标文件 {path} 不是合法 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise MetricValidationError(f"指标文件 {path} 的顶层必须是键值结构")
    return data


_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _parse_metric(entry: dict, where: str) -> Metric:
    """解析单个指标并做结构校验。缺什么就报什么，不留到运行时。"""
    name = str(entry.get("name") or "").strip()
    if not name:
        raise MetricValidationError(f"{where} 缺少 name")
    if not name[0].isascii() or name[0].isdigit() or not set(name) <= _NAME_OK:
        raise MetricValidationError(
            f"{where} 的 name {name!r} 不合法：只能是字母/数字/下划线、"
            f"不以数字开头（中文请填到 label 与 synonyms）。"
        )

    label = str(entry.get("label") or "").strip()
    if not label:
        raise MetricValidationError(f"{where}（{name}）缺少 label（展示名）")

    expr = str(entry.get("expr") or "").strip()
    if not expr:
        # 这是 spec 明确要求的场景：缺公式必须报错，且不能静默跳过该指标
        raise MetricValidationError(
            f"{where}（{name}）缺少 expr（计算公式）。"
            f"没有口径的指标无法被引用，也不会被静默忽略。"
        )
    # 公式语法必须在**加载时**就验：写错了要和"缺字段"一样早暴露，
    # 不能等到某条流程引用它、或者 dbt 跑到一半才由 PostgreSQL 报出来。
    # 这里只为校验，列清单由 Metric.columns 按需重算（指标数量级很小，不值得缓存）。
    try:
        extract_columns(expr)
    except MetricValidationError as exc:
        raise MetricValidationError(f"指标 {name!r} 的公式有问题：{exc}") from exc

    dataset = str(entry.get("dataset") or "").strip()
    if not dataset:
        raise MetricValidationError(f"{where}（{name}）缺少 dataset（所属数据集）")

    owner = str(entry.get("owner") or "").strip()
    if not owner:
        raise MetricValidationError(
            f"{where}（{name}）缺少 owner（负责人）。"
            f"口径统一本质上是组织问题：没有负责人的指标，"
            f"数字对不上时没人能裁定。"
        )

    return Metric(
        name=name,
        label=label,
        expr=expr,
        dataset=dataset,
        owner=owner,
        synonyms=_str_tuple(entry.get("synonyms"), where, name),
        grain=_str_tuple(entry.get("grain"), where, name),
        unit=str(entry.get("unit") or "").strip(),
        description=str(entry.get("description") or "").strip(),
    )


def _str_tuple(value, where: str, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),)
    if not isinstance(value, (list, tuple)):
        raise MetricValidationError(f"{where}（{name}）的同义词/粒度必须是列表")
    return tuple(str(v).strip() for v in value if str(v).strip())


def _check_against_datasets(
    metric: Metric, datasets: Mapping[str, Sequence[str]]
) -> tuple[str, bool]:
    """返回 (告警文本, 是否不可用)。列不存在是硬错误，直接抛。"""
    columns = datasets.get(metric.dataset)
    if columns is None:
        return (
            f"指标「{metric.label}」({metric.name}) 锚定的数据集 "
            f"「{metric.dataset}」尚未上架，该指标暂不可用"
            f"（开发环境可跑 python scripts/seed_metrics_demo.py 准备演示数据）",
            True,
        )

    known = set(columns)
    missing = [c for c in metric.columns if c not in known]
    if missing:
        raise MetricValidationError(
            f"指标「{metric.label}」({metric.name}) 的公式引用了数据集 "
            f"「{metric.dataset}」里不存在的列：{', '.join(missing)}。"
            f"该数据集现有列：{', '.join(columns) or '（空）'}。"
            f"请改名指标公式，或先在数据集里补上这些列。"
        )
    return "", False


def dataset_columns(session) -> dict[str, list[str]]:
    """从库里读「数据集名 → 列清单」，作为 `load_dictionary` 的语义校验输入。

    用**全部已启用的数据集**，而不是某个用户可见的那些：这里校验的是
    「指标定义与库结构是否一致」，与权限无关 —— 权限管的是"谁能看到哪些行"，
    不该影响"口径写得对不对"。
    """
    from app.models import Dataset

    return {
        ds.name: list(ds.columns or [])
        for ds in session.query(Dataset).filter_by(enabled=True).order_by(Dataset.id).all()
    }


def load_for(session, path: str | Path | None = None) -> MetricDictionary:
    """生产链路的便捷入口：加载字典并带上库里的数据集做完整校验。

    两个消费方（`flow/compile.py`、`assistant/prompts.py`）都走这里，
    保证"同一问题、同一口径、同一结果"。
    """
    return load_dictionary(path, datasets=dataset_columns(session))


__all__ = [
    "DEFAULT_RELATIVE_PATH",
    "Metric",
    "MetricDictionary",
    "dataset_columns",
    "default_path",
    "extract_columns",
    "load_dictionary",
    "load_for",
]
