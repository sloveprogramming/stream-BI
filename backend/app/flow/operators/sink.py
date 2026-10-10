"""输出算子。

落点（写到哪个 schema、表名、要不要注册成数据集）统一在 `FlowOutput` 里配置，
不用算子参数重复一遍 —— 一处定义，避免"节点参数和输出设置不一致"这种坑。
"""
from pydantic import BaseModel, ConfigDict

from app.flow.operators.base import Operator, register


class MaterializeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register
class SinkMaterialize(Operator):
    type = "sink.materialize"
    category = "输出"
    title = "物化落表"
    description = "结果写入 dwd/dws/ads，并注册为数据集（自动继承权限与四类出口）；落点在上方输出设置里配置"
    Params = MaterializeParams

    def compile_sql(self, ctx, node) -> str:
        self.parse(node)
        return f"SELECT * FROM {ctx.input(node)}"
