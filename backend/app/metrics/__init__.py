"""指标字典：口径的唯一来源。

对外只暴露这几样：加载（`load_dictionary` / `load_for`）、结构（`Metric` /
`MetricDictionary`）、歧义检测（`ambiguous_metrics`）。

消费方：
- `app/flow/compile.py` —— 把 `field.derive` 引用的指标在编译期展开成公式；
- `app/assistant/*` —— 把口径注入问数上下文，并在歧义时反问。
"""

from app.metrics.ambiguity import ambiguous_metrics, clarification_for
from app.metrics.errors import (
    MetricError,
    MetricNotFoundError,
    MetricUnavailableError,
    MetricValidationError,
)
from app.metrics.loader import (
    DEFAULT_RELATIVE_PATH,
    Metric,
    MetricDictionary,
    dataset_columns,
    default_path,
    extract_columns,
    load_dictionary,
    load_for,
)

__all__ = [
    "DEFAULT_RELATIVE_PATH",
    "Metric",
    "MetricDictionary",
    "MetricError",
    "MetricNotFoundError",
    "MetricUnavailableError",
    "MetricValidationError",
    "ambiguous_metrics",
    "clarification_for",
    "dataset_columns",
    "default_path",
    "extract_columns",
    "load_dictionary",
    "load_for",
]
