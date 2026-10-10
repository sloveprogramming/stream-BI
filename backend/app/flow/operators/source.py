"""输入算子。"""
from pydantic import BaseModel, ConfigDict, Field

from app.flow.operators.base import Operator, register


class DatasetSourceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: int = Field(description="数据集 id（只能读你有权限的数据集）")
    alias: str | None = Field(default=None, description="该输入的别名（可选，便于阅读）")


@register
class DatasetSource(Operator):
    type = "source.dataset"
    category = "输入"
    title = "读取数据集"
    description = "从已注册的数据集读取；行级与字段级权限在编译时自动套用"
    Params = DatasetSourceParams

    def compile_sql(self, ctx, node) -> str:
        # 参数先过一遍校验，错误信息才指向具体字段
        self.parse(node)
        # 唯一入口：由编译器注入的 loader 负责套行级权限，算子拿不到裸表名
        return ctx.scoped_source(node)
