# Tasks: 指标字典

## 1. 指标定义与加载

- [x] 1.1 在 `backend/dbt/models/marts/metrics.yml` 写 3 个指标定义
  （毛利率 / 动销率 / 到货及时率，分别属于销售、商品、采购侧）
  验收：`dbt parse --project-dir dbt --profiles-dir dbt` 通过且无新增报错
  **实现记录**：顶层键用项目自己的 `metrics_dictionary`，**不是** MetricFlow 语义模型
  （实测后改的，原因见 design 的「实现记录」）。`dbt parse` 实测忽略该键、exit=0。

- [x] 1.2 实现 `backend/app/metrics/loader.py`：读取指标定义并校验
  （名称唯一、公式非空、所属数据集存在、引用的列存在、负责人非空）
  验收：`tests/unit/test_metrics_loader.py` 断言「缺公式」与「引用不存在的列」两条错误路径
  **实现记录**：校验分两层 —— 结构校验（无外部依赖，必跑）与语义校验（带数据集清单时跑）。
  「数据集未上架」不阻塞加载，标记为不可用并告警（数据集是运行时上架的，
  强制"必须已存在"会变成部署顺序陷阱）；被引用时才拒绝。

- [x] 1.3 实现按名称 / 同义词检索指标
  验收：单测断言「毛利」能命中「毛利率」

- [x] 1.4 定义加载结果为不可变结构（含名称、展示名、同义词、公式、数据集、粒度、负责人）
  验收：单测断言返回结构字段完整

## 2. 编排侧引用

- [x] 2.1 `field.derive` 参数增加 `metric`，与 `expr` 二选一，
  同时提供或都不提供时在参数校验阶段就报错
  验收：单测断言两种非法组合都被拒绝

- [x] 2.2 `compile.py` 编译 `field.derive` 时，若为指标引用则展开为公式 SQL
  验收：单测断言生成的 SQL 含指标公式，且 DSL 里存的仍是指标名

- [x] 2.3 未知指标的错误信息列出可用指标名
  验收：单测断言错误信息至少包含一个可用指标名

- [x] 2.4 校验指标引用的列是否在上游输出中，不在则编译期报错
  验收：单测断言「流程丢弃了指标要用的列」时编译失败且信息可读

- [x] 2.5 端到端：`backend/scripts/flow_smoke.py` 增加一条引用指标的流程
  验收：`python scripts/flow_smoke.py --cleanup` 跑通，物化结果里该指标列有值
  **实测结果**：两条流程均 `ok`。口径链物化出
  `[(1, 0.40), (2, 0.25), (3, 0.10)]`，与源数据 `(amount, cost)` 吻合；
  生成的 SQL 里含 `(amount - cost) / nullif(amount, 0)`。

## 3. 问数侧对齐

- [x] 3.1 `app/assistant/prompts.py` 注入指标清单（名称 / 展示名 / 同义词 / 口径）
  验收：单测断言组装出的 system prompt 含指标定义与同义词
  **实现记录**：口径与表结构在 `schema_context.build_schema_context` 里一起构建，
  三个消费方（chat / agent / eval）自动都拿到，不用各自加载字典。

- [x] 3.2 同一概念存在多口径时走 `clarify` 反问，而不是任选一个
  验收：单测断言「上月销售额」在双口径存在时返回澄清请求
  **实现记录**：判定由字典做（`app/metrics/ambiguity.py`，纯字符串、确定性、可单测），
  不交给模型 —— 让模型自行判断该用哪个口径，正是它会自信地给出错数的地方。
  测试用"被调用就报错"的假 LLM 证明：歧义时模型一次都没被问到。

- [x] 3.3 生成 SQL 使用字典口径
  验收：在 `backend/evals/` 评测集里补 2 条口径类问题，跑一次评测并记录准确率
  **实测结果**：用例已补（`evals/cases.json`：metric_001 毛利率、metric_002 动销率）。
  评测已跑，但**本机未配置 `BISQL_LLM_API_KEY`**，2 条均为 `llm_error`
  （`Missing credentials`），**准确率数字无法得出，不做编造**。
  已验证的部分：schema 构建、用例解析、指标口径确实进入了 prompt
  （`tests/unit/test_assistant_metrics.py`）。
  补齐方式：配置 key 后执行 `python -m app.assistant.eval --cases evals/cases.json`。

## 4. 收尾

- [x] 4.1 全量 `cd backend && python -m pytest -q` 通过
  验收：通过数不低于改动前，无新增失败 —— 改动前 316，现在 **366 passed**（+50）

- [x] 4.2 `alembic check` 无差异（本 change 预期不新增表；若实际需要落库则补迁移）
  验收：输出为 `No new upgrade operations detected.` —— **未新增表**，符合预期

- [x] 4.3 更新 `README.md` 的「约定」一节：新增指标走 `metrics.yml`，不要在流程里手写公式
  验收：README 里能找到这条约定

- [x] 4.4 `openspec validate --all` 全部通过后再归档
  验收：`summary.failed` 为 0
