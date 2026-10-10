# Design

## Context

**编排链路现状**：画布产出 DSL → `backend/app/flow/compile.py` 把节点编译成 CTE
→ `dbt build` 物化到 `dwd/dws/ads` → 产物注册为数据集。
其中 `field.derive` 目前接受裸 SQL 表达式（`app/flow/operators/field.py`），
编译时用 `validate_expr()` 校验后直接内联。

**dbt 项目现状**：`backend/dbt/` 已有 `dbt_project.yml`、`profiles.yml`、
`macros/generate_schema_name.sql`；`dbt-core 1.12.5` + `dbt-postgres 1.11.0` 已装，
MetricFlow 作为 dbt-core 的依赖已一并安装。`models/marts/` 目录已建（只有 README）。

**问数链路现状**：`app/assistant/prompts.py` 组装 system prompt，
`app/assistant/schema_context.py` 注入表结构与样例行，
`app/assistant/agent.py` 的 LangGraph 图里已有 `clarify` 节点（Step 4 实现）。

**约束**（来自 `openspec/config.yaml`）：只加不换、不自研格式、不引中间件、
物理列名 ASCII、改模型必须配迁移。

## Goals / Non-Goals

**Goals:**

- 指标口径只有一个来源，且这个来源随代码走、可评审、可追溯到负责人
- 编排与问数引用同一份口径，不需要各自复制公式
- 口径变更的影响范围可控：改指标 → 下次编译生效，不用改流程

**Non-Goals:**

- 不做指标的可视化编辑器
- 不做独立的语义层服务
- 不做指标血缘（独立 change）
- 不做指标的实时计算

## Decisions

### 决策 1：用 dbt 语义模型（MetricFlow），不自研 YAML 格式

指标定义落在 `backend/dbt/models/marts/metrics.yml`，用 dbt 的语义模型格式。

**理由**：自研格式没有生态——没有工具、没有文档、招不到认识它的人。
MetricFlow 已经是 dbt 生态的一部分，与 `dbt build` 走同一条链路，
将来接 BI 工具也是现成的。

**已考虑并否决的方案**：自研 `metrics.yaml` + 自己写解析器。
那等于把「格式设计」这件已经有了标准答案的事重做一遍，且无法被工具消费。

### 决策 2：指标引用在**编译期展开**，不在运行期解析

DSL 里存的是指标名；`compile.py` 编译时把它替换成公式 SQL。

**理由**：

- dbt model 是静态 SQL，运行期没有地方放「解析指标」这一步；
- 编译期展开后生成的 SQL 是自解释的——人一看就知道用的是哪个公式，
  而且编译产物留档在 `data_flow_versions` 里，可追溯；
- 与既有的权限裁剪是同一个位置、同一种做法（编译期把「声明」变成「确定的 SQL」），
  不引入第二种心智模型。

**代价（且是期望行为）**：指标改了之后，历史版本的编译产物不会自动更新。
历史版本本来就该冻结——不能今天看 v1 的编译结果、明天发现它变了。

### 决策 3：问数对齐用「注入 + 反问」，不指望模型自觉

把指标清单（名称、展示名、同义词、口径）注入 system prompt；
当同一概念存在多个口径时，**复用已有的 `clarify` 节点**反问。

**理由**：模型没有企业先验知识，靠它"猜对口径"不现实。
而这个项目已经实现了 `clarify`（`agent.py` 里的 `clarify_node`），
直接复用，不新增流程节点、不新增状态字段。

**已考虑并否决的方案**：让模型在 prompt 里自行判断该用哪个口径。
那正是当前的问题所在——它会自信地给出一个错的数。

### 决策 4：一个加载入口，多个消费方

新增 `backend/app/metrics/loader.py`，被两处调用：
`compile.py`（编排引用）与 `assistant/prompts.py`（问数注入）。

**理由**：与权限裁剪器同一个思路——**一个来源、多个消费方**，
从架构上保证「同一问题、同一口径、同一结果」。
如果两处各解析一次 YAML，迟早会出现"编排用的是旧版本、问数用的是新版本"。

### 决策 5：加载即校验，不留到编译期

`loader.py` 在加载时就校验：名称唯一、公式非空、引用的数据集与列存在、负责人非空。

**理由**：与 `service.py`「保存即编译」的既定做法一致——
让问题在**最早能发现的地方**暴露，而不是等半夜跑批失败。

## Risks / Trade-offs

- **MetricFlow 对 dbt-postgres 的支持需要实测**。`dbt build` 已经跑通，
  但语义模型解析是另一条代码路径。**退路**：若 `dbt parse` 无法加载语义模型，
  就把指标定义写成项目自己的 YAML，但**保留 dbt 项目的目录位置与文件命名**，
  这样将来 MetricFlow 支持了就只是换解析器，不动文件。

- **指标公式与物理列可能不同步**。流程把某个列重命名或丢弃后，
  引用该列的指标就会失效。**应对**：加载阶段校验列是否存在；
  另外在 `compile.py` 里把「指标引用的列不在上游输出中」也作为一种编译错误报出来。

- **口径统一本质上是组织问题，不是技术问题**。技术只能提供载体与追溯能力。
  真正的口径裁定需要业务方参与。**应对**：指标定义里强制填 `owner`，
  让「这个数该问谁」有明确答案；后续可在此基础上做指标负责人机制。

- **本 change 预期不新增数据库表**。指标定义放在文件里（随代码走），
  不落库。如果后续需要「指标使用统计」再单独提 change。
