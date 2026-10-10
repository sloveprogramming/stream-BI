"""AI 能力层。

三层，各管一件事：

    client.py     单次调用：结构化输出（Pydantic 约束 + 失败回灌重试）
    operators.py  批量管道：去重 → 缓存 → 分批并发
    tasks/        领域提示词与结果结构（按任务分，见 flow/operators/ai.py 的使用）

依赖方向：`ai/` 不依赖 `flow/`。编排层调 AI，AI 不认识编排。
"""

from app.ai.errors import AiError, AiUnavailableError, StructuredOutputError  # noqa: F401

__all__ = ["AiError", "AiUnavailableError", "StructuredOutputError"]
