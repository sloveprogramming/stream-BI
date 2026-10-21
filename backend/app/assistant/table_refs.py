"""数据集引用名解析：把模型写的表名映射回**唯一的**物理数据集。

## 为什么需要这个模块

`schema_context` 渲染 prompt 时用**数据集名**（`ds.name`，如 `demo_sales`），
而安全闸门与权限重写用**物理表名**（`ds.source_table`，如 `demo.sales_order`）。
两边不一致时，模型按 prompt 写的表名会被闸门判为「无权限的表」——问数必然失败。

在「数据集名恰好等于表名且无 schema 前缀」的老场景下不会暴露，
但一旦两者不同（这是常态），问数全盘不可用。

## 为什么不是"两边都认"那么简单

`rewrite_sql` 必须知道每个引用该替换成**哪一张**物理表，才能注入对应的行级
规则。如果允许两个不同的引用名指向同一张物理表，就必须保证「引用名 → 物理表」
是**一对一**的，否则会出现"用 A 的名字读到 B 表的行级规则"这类越权。

所以本模块的核心是**构建别名表时检测歧义，发现冲突直接拒绝**，
而不是"能查到就用"。
"""
from __future__ import annotations

from dataclasses import dataclass

# 模型可能写的几种引用形式，按优先级
#   1. 数据集名          demo_sales
#   2. 物理表全名        demo.sales_order
#   3. 物理表短名        sales_order      （仅当全局唯一时才认，见下）
#
# 短名要格外小心：不同 schema 下可能有同名表。所以只有当短名在整个
# 可见集合里唯一时才注册，否则不注册（模型写短名会被闸门拒绝，
# 这是**正确**的行为——宁可让它报错重写，也不要猜错表）。


class AmbiguousTableError(Exception):
    """两个引用名指向了不同的物理表，或同一短名对应多张表。"""


@dataclass(frozen=True, slots=True)
class TableRef:
    """一张可见表的引用信息。"""

    dataset_name: str  # 数据集名（prompt 里展示的）
    source_table: str  # 物理表名 schema.table
    short_name: str  # 物理表短名 table

    @property
    def aliases(self) -> tuple[str, ...]:
        """该表可被引用名（按优先级）。空串已过滤。"""
        names = [self.dataset_name, self.source_table, self.short_name]
        return tuple(dict.fromkeys(n for n in names if n))


def normalize(ref: str) -> str:
    """引用名归一化：去空白、统一小写。

    PostgreSQL 未加引号的标识符不区分大小写，模型有时写大写，
    归一化后再比对，避免因此误判为"无权限"。
    """
    return (ref or "").strip().lower()


def build_alias_map(tables: list[dict]) -> dict[str, str]:
    """构建「引用名(小写) → 物理表名」的映射。

    三个来源都注册：数据集名、物理表全名、物理表短名（仅唯一时）。

    冲突处理（这是本函数的关键）：
    - 同一个引用名被两张不同的表占用 → 抛 `AmbiguousTableError`。
      这种情况**必须报错而不是任选一个**：选错就是越权或查错数据。
    - 短名与某个数据集名撞车 → 同理报错。

    `tables` 是 `schema_context.build_schema_context()` 产出的列表，
    每项至少含 `name` 与 `source_table`。
    """
    alias_map: dict[str, str] = {}
    # 短名出现次数：只有唯一短名才注册，避免 schema 间同名表互相覆盖
    short_counts: dict[str, int] = {}
    refs: list[TableRef] = []

    for t in tables:
        source = t.get("source_table") or ""
        if not source:
            continue
        short = source.split(".")[-1]
        ref = TableRef(
            dataset_name=t.get("name") or "",
            source_table=source,
            short_name=short,
        )
        refs.append(ref)
        short_counts[normalize(short)] = short_counts.get(normalize(short), 0) + 1

    for ref in refs:
        # 数据集名与物理表全名一定要注册（这两个是明确无歧义的）
        names = [ref.dataset_name, ref.source_table]
        # 短名只在全局唯一时注册
        if short_counts.get(normalize(ref.short_name), 0) == 1:
            names.append(ref.short_name)

        for name in names:
            key = normalize(name)
            if not key:
                continue
            existing = alias_map.get(key)
            if existing is not None and existing != ref.source_table:
                raise AmbiguousTableError(
                    f"引用名 {name!r} 同时指向 {existing!r} 与 "
                    f"{ref.source_table!r}，无法确定用哪个。"
                    f"请改用完整的数据集名或 schema.table 形式。"
                )
            alias_map[key] = ref.source_table

    return alias_map


def resolve(ref: str, alias_map: dict[str, str]) -> str | None:
    """把一个引用名解析成物理表名；解析不到返回 None。"""
    return alias_map.get(normalize(ref))


def canonical_names(tables: list[dict]) -> set[str]:
    """所有可被引用的名字（小写），供闸门做"认识这个表吗"的判断。"""
    return set(build_alias_map(tables).keys())


__all__ = [
    "AmbiguousTableError",
    "TableRef",
    "build_alias_map",
    "canonical_names",
    "normalize",
    "resolve",
]
