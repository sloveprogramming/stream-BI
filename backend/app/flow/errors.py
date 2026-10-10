"""编排层的异常类型。

分四类是为了让调用方能分别处理、且**不允许静默降级**：

- `FlowValidationError` —— DSL 本身有问题（成环、孤立节点、多于一个输出）→ 400
- `FlowCompileError`    —— 算子/参数组合出不来 SQL（缺列信息、表达式非法）→ 400
- `FlowPermissionError` —— owner 对源数据集无授权 → 403，
  且**绝不允许降级成"不过滤继续跑"**：那等于把权限体系绕过去了
- `FlowExecutionError`  —— 引擎报错、超时、产物注册失败 → 500
"""


class FlowError(Exception):
    """编排层异常基类。"""


class FlowValidationError(FlowError):
    """DSL 校验不通过。"""


class FlowCompileError(FlowError):
    """编译失败。"""


class FlowPermissionError(FlowError):
    """权限不足：编排读源必须过裁剪器，拿不到授权就中止。"""


class FlowNotFoundError(FlowError):
    """流程/版本不存在，或当前用户无权看到（两者统一返回 404，不泄露存在性）。"""


class FlowExecutionError(FlowError):
    """执行失败。"""
