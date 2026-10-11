# Proposal: 向量算子（embed.text / dedup.semantic）

## Why

编排现在能做「过滤、去重、JOIN、分类、抽取」，但**语义层面还是空白**：

- **同一件商品写法不同，精确去重认不出来**。ERP 里「新农哥碧根果」和
  「新农哥碧根果（袋装）」是两条记录，`clean.dedup` 按列值比对永远判成两个 SKU。
  这是主数据治理里最反复出现的一类脏数据，也是运营每月手工合并的活；
- `llm.classify` 能解决「商品标题 → 类目」，但解决不了「这两条是不是同一个东西」——
  那是两两比对的问题，N² 次调 LLM 成本不成立；
- 向量检索（`retrieve.vector`）将来要落在 pgvector 上，但**向量从哪来**得先有答案。

## What Changes

- 新增 `embed.text` 算子：文本列 → 向量列（落表，供下游与将来的向量检索用）；
- 新增 `dedup.semantic` 算子：按向量相似度**标记**语义分组，输出组号与组内代表行标记；
- 新增 `backend/app/ai/embedding.py`：**提供方抽象** —— 本地模型（BGE-M3）/ HTTP API 二选一；
  两者都不可用时**中止并给出两条出路**，绝不静默降级（降级成字符相似度会让用户
  以为自己拿到的是语义结果）；
- 复用既有成本三控（去重 / 缓存 / 分批并发）：向量同样进缓存，cache_key 含模型名。

## Capabilities

### New Capabilities

- `embedding-operators`：文本向量化与语义去重，含提供方探测与不可用时的明确拒绝

### Modified Capabilities

- （无）既有 `data-flow` 的契约不被改变：新增算子不改变流程语义，
  而 AI 算子的成本约束已由 `data-flow` 的 `LLM 算子的成本可控` 覆盖。

## Impact

- 新增 `backend/app/ai/embedding.py`（提供方抽象 + 本地/API 实现 + 环境探测）
- 新增 `backend/app/ai/similarity.py`（余弦相似度、并查集聚类）
- 改动 `backend/app/flow/operators/ai.py`（新增两个 `engine=python` 的算子）
- 改动 `backend/app/settings.py`（embedding 提供方 / 模型 / API 地址等配置）
- `backend/pyproject.toml` 加 `numpy`（相似度计算与本地模型都依赖它，约 15MB，无传递依赖）
- 新增 `backend/scripts/check_embedding.py`：一条命令看当前环境的 embedding 可用性

## 不做什么（Out of Scope）

- **不做 `retrieve.vector`（向量检索）**。它要求把向量持久化进 pgvector 并建 ANN 索引，
  而本机 PostgreSQL（Windows 原生编译）**没有 vector 扩展**——实测可用扩展只有
  `pg_trgm` / `unaccent`。基础设施的事单独提 change，不塞进这里。
- **不做 rerank、不做混合检索**。它们建立在检索之上，检索本身还没有。
- **不内置模型权重**。2.2GB 的东西不该进代码仓库；用 `scripts/check_embedding.py`
  告诉用户怎么准备（国内镜像 / API 两条路）。
- **不做图像 / 多模态向量**。当前需求是文本语义。
- **不在数据库里存 pgvector 类型**。向量以 JSON 文本落列，普通 PostgreSQL 就能跑。
