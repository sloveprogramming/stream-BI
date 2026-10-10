手写的 marts 层：面向应用的宽表，以及指标口径词典。

约定：
- 宽表命名 `ads_<主题>.sql`，一个主题一个文件；
- 指标口径放 `metrics.yml`，**口径只在这里定义一次**，
  画布的派生字段（`field.derive` 填 `metric`）与问数都应引用它，
  不要在 SQL 里各写各的公式；
- `metrics.yml` 的顶层键是 `metrics_dictionary`，由 `backend/app/metrics/loader.py`
  读取。**不用 MetricFlow 语义模型格式**：它的 metric 是聚合度量（`sum(amount)`），
  而编排要的是行级表达式（`amount - cost`），层次对不上。详见 loader.py 模块头部。
- 数据集的「字段注释 / 展示名」在数据集元数据里维护（`datasets.column_comments`），
  物理列名保持 ASCII。
