"""AI 能力层的异常类型。"""


class AiError(Exception):
    """AI 能力层异常基类。"""


class AiUnavailableError(AiError):
    """LLM / 模型不可用（未配置 key、网络不通、模型未安装）。"""


class StructuredOutputError(AiError):
    """模型没有按约定的结构输出，且重试后仍失败。"""
