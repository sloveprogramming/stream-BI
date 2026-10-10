"""算子库。

导入本包即完成算子注册（各子模块在 `@register` 时写入 `base.REGISTRY`）。
**顺序要紧**：先 `base`，再各算子模块 —— 否则子模块会去导入还没建好的 `base`。
"""
from app.flow.operators.base import (  # noqa: F401
    REGISTRY,
    Operator,
    get_operator,
    operator_catalog,
    register,
)

# 导入即注册。新增算子模块时在这里挂一行。
from app.flow.operators import source  # noqa: F401,E402
from app.flow.operators import field  # noqa: F401,E402
from app.flow.operators import clean  # noqa: F401,E402
from app.flow.operators import join  # noqa: F401,E402
from app.flow.operators import agg  # noqa: F401,E402
from app.flow.operators import sink  # noqa: F401,E402

__all__ = ["REGISTRY", "Operator", "get_operator", "operator_catalog", "register"]
