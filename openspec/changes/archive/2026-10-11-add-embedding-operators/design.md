# Design

## Context

**编排链路现状**：`flow/operators/ai.py` 已有 3 个 `engine=python` 的算子
（`llm.classify` / `llm.judge` / `llm.extract`），执行方式统一是
「编译到本节点为止的 SELECT → 取数（已套行级权限）→ 算子处理 → 返回 (列名, 行)
→ 执行器物化成表 → 下游 dbt JOIN 它」。
成本三控（去重 / 缓存 / 分批并发）在 `app/ai/operators.py::run_llm_batch`。

**本机环境实测**（决定了下面几乎所有决策）：

| 探测项 | 结果 |
|---|---|
| `torch` / `transformers` / `FlagEmbedding` / `numpy` | 全部未安装 |
| `openai` | 已安装 |
| `huggingface.co` | **超时不可达** |
| `hf-mirror.com` / `modelscope.cn` / `pypi.org` | 可达 |
| PostgreSQL 16.15（Windows 原生编译）可用扩展 | 只有 `pg_trgm` / `unaccent`，**没有 `vector`** |

**约束**（来自 `openspec/config.yaml`）：只加不换、不自研格式、不引中间件、
物理列名 ASCII、改模型必须配迁移、取数只走 `app/assistant/tools.py`。

**复用的现有模块**：

- `app/ai/operators.py::run_llm_batch` —— 去重 + 缓存 + 分批并发，向量直接复用
- `app/ai/errors.py::AiUnavailableError` —— 「环境没准备好」这一类的统一异常
- `app/flow/operators/base.py::Operator.run_python` —— AI 算子接缝（吃数据、不碰连接）
- `app/flow/operators/ai.py::_require_column` / `_check_output_column` / `_wrap_ai_errors`
- `app/models/ai.py::AiLlmCache` —— 缓存的落点（`output` 是 JSONB，放向量没问题）
- `app/settings.py::Settings` —— 配置统一走 `BISQL_` 前缀

## Goals / Non-Goals

**Goals:**

- 语义去重在**本机现有条件**下可用（无 GPU、无 pgvector、HuggingFace 不通）
- 「提供方不可用」是一个**明确的中止**，不是悄悄换算法
- 向量与分组结果可解释：用户能看见哪些被合并了、为什么

**Non-Goals:**

- 不做向量检索（`retrieve.vector`）与 ANN 索引
- 不做 rerank / 混合检索
- 不把模型权重放进仓库
- 不做多模态

## Decisions

### 决策 1：提供方抽象 + 明确拒绝，绝不静默降级

`app/ai/embedding.py` 暴露一个 `embed_texts(texts, *, model="")`，
内部按 `BISQL_EMBEDDING_PROVIDER` 选择实现：

| 配置值 | 行为 |
|---|---|
| `auto`（默认） | 配了 API 地址 → API；否则本机能 import 本地依赖 → 本地；都没有 → **报错** |
| `api` | 只用 HTTP API（兼容 OpenAI `/embeddings`） |
| `local` | 只用本地模型（BGE-M3）；缺依赖就报错，**不退回 API** |
| `none` | 禁用，用于「本环境不打算用向量」时给出清晰提示 |

**为什么必须这么做**：本机两条路都不通（没装依赖、没配 API）。
如果这里"聪明地"退化成字符相似度，用户会以为自己在做语义去重，
拿到一批错的分组却毫无察觉 —— 那正是本项目最反感的那类静默降级。
错误信息里直接给两条出路（装依赖 / 配 API），让它成为一次可操作的失败。

### 决策 2：`dedup.semantic` 只标记分组，不删行

输出 `semantic_group`（组号）与 `is_representative`（是否组内代表）两列，
**输出行数与输入相同**。要只留代表行，接一个 `clean.filter` 即可。

**理由**：删行是不可逆的信息损失，标记是可解释的。用户能先看见"哪些被合并了"，
确认无误再决定丢不丢。而且划清与既有 `clean.dedup` 的边界 —— 那个是精确去重、会删行；
这个是语义分组、只加标记。

### 决策 3：向量以 JSON 文本落列，不碰 pgvector

1024 维 float 序列化成 `[0.1,0.2,...]`（保留 6 位小数）存进 text 列。

**理由**：本机 PG 没有 `vector` 扩展（实测），而"能跑"优先于"跑得漂亮"。
代价是体积（约 6KB/行）与无法在 SQL 里算距离 —— 但距离计算本来就在 Python 侧做
（决策 4），SQL 侧只需要"能存能取"。

### 决策 4：相似度用 numpy 向量化 + 分块，不用纯 Python

N² 次点积，纯 Python 在 1024 维下每次约 20µs，5000 行就是 1250 万次 ≈ 数十秒到几分钟；
换成矩阵乘后是毫秒级。所以引入 `numpy`（约 15MB，无传递依赖，
而且本地模型路径本来也要它）。

内存上分块处理（每块 1000 行 × 全量），峰值约 40MB，不把整个 N×N 矩阵摊开。

### 决策 5：聚类用并查集连通分量，不用贪心质心

「相似度 ≥ 阈值」的连边，取连通分量作为分组。
它直接对应阈值的语义（传递闭包），结果**与处理顺序无关** ——
贪心质心会因遍历顺序不同给出不同分组，那种不确定性在数据治理里很难跟业务解释。

**代价（已知且接受）**：链式合并 —— A≈B、B≈C 但 A≉C 时三者仍同组（"单链"问题）。
对本项目要解决的场景（同一商品的不同写法）是合适的：它们本来就围绕一个语义中心。
真出现反例再考虑加 `min_similarity` 约束，现在不做。

### 决策 6：代表行默认取组内**最短**文本

「新农哥碧根果」vs「新农哥碧根果（袋装）」里，短的那个通常是规范写法。
做成参数 `representative: shortest | longest | first`，默认 `shortest`。

**理由**：代表行的用途是回填主数据，短名更可能被业务当作标准名。

### 决策 7：复用 `run_llm_batch` 做缓存与并发

向量也是"文本 → 结果"，直接走同一条管道：`cache_key` 含
`算子类型 | 提供方 | 模型名`，所以换模型必然重新算（spec 的显式场景）。

**注意**：`run_llm_batch` 的名字里有 `llm`，但它做的事情（去重/缓存/分批并发）
与 LLM 无关。改名会牵动 3 个既有算子，收益不抵风险 —— 在函数文档里补一句说明即可。

## Risks / Trade-offs

- **本地模型在 CPU 上慢**。BGE-M3 无 GPU 时 5000 行可能要几分钟，
  且首次会下载 2.2GB。**应对**：`scripts/check_embedding.py` 让用户先看清代价再决定；
  算子沿用既有的输入行数上限（超限中止而非截断）。

- **默认阈值 0.92 是经验值**，可能对某类数据偏高（漏合并）或偏低（误合并）。
  **应对**：阈值是算子参数，用户可在画布上调；设计上宁可漏合并也不误合并
  （误合并会污染主数据，漏合并只是回到人工）。

- **并查集的链式合并**（决策 5）。**应对**：在算子描述里写明这个特性，
  用户可先用较小的阈值观察效果。

- **向量列体积大**（约 6KB/行）。**应对**：向量列只在该用的时候产出；
  `dedup.semantic` 可以只输出分组结果（`emit_vectors=False` 默认不落向量列）。

- **本机没有任何提供方**。**应对**：这不是缺陷而是本次要显式处理的状态 ——
  代码与测试覆盖"不可用即拒绝"这条路径；真跑由用户二选一准备环境。
