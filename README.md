# BIsql 数据中台

为企业 ERP 数据构建的统一权限分发中台：让业务人员与大模型 Agent 在**同一套权限口径**下安全取数。

- 需求与演进：[docs/2026-10-10-BIsql数据中台改造方案.md](docs/2026-10-10-BIsql数据中台改造方案.md)
- 技术选型：[docs/2026-10-10-BIsql数据中台技术方案-v2-主流技术栈.md](docs/2026-10-10-BIsql数据中台技术方案-v2-主流技术栈.md)
- 模块实现设计：[docs/2026-10-10-BIsql模块实现设计.md](docs/2026-10-10-BIsql模块实现设计.md)
- 原始设计文档：docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md

## 核心设计：一套裁剪器，所有出口复用

行级规则 + 字段白名单由**同一个查询构造器**（`app/access/`）负责，
REST API / Excel·CSV 导出 / MCP 工具 / 自然语言问数 / **数据编排产物** 全部经过它。
所以「同一问题、同一权限、同一结果」是架构保证，不是靠人守规范。

数据编排（`app/flow/`）把画布上的流程编译成 dbt model 时，
源节点会被替换成**带行级权限 WHERE 的子查询**，算子层拿不到裸表名 ——
编排在结构上不可能绕过权限。

## 目录

```
backend/
├── app/
│   ├── access/      权限元数据与查询裁剪（行级 + 字段级）
│   ├── api/         REST 接口（datasets / flows / admin / reports / grants / quality …）
│   ├── assistant/   自然语言问数、LangGraph Agent、置信度、评测
│   ├── flow/        数据编排：DSL / 算子库 / 编译器 / 执行器
│   ├── metrics/     指标字典：口径加载、校验、检索、歧义判定、指标血缘
│   ├── monitor/     主动监控与异常归因
│   ├── sync/        ERP → ods 增量同步（水位线 + 结构自动演进）
│   ├── tenancy.py   租户上下文与隔离
│   ├── grants.py    授权有效期与状态流转
│   ├── quality.py   数据质量巡检执行器
│   └── models/      SQLAlchemy 模型
├── dbt/             dbt 项目（编排生成的 model 落在 models/generated/）
└── scripts/         运维与冒烟脚本
frontend/            React + TypeScript 管理台
```

## 本地开发

1. 启动依赖（二选一）：本机原生 PostgreSQL 16（`localhost:55432`，默认连接串已指向它）；或 `docker compose up -d`（PG 55432 / ERP MySQL 53306）
2. `cd backend && python -m pip install -e ".[dev]"`
3. `copy .env.example .env`（按需改连接串）
4. `alembic upgrade head`
5. `python -m uvicorn app.main:app --reload`
6. 健康检查：GET http://127.0.0.1:8000/api/v1/health

## 测试

```
cd backend && python -m pytest -v
```

## 规格驱动开发（OpenSpec）

本项目用 [OpenSpec](https://github.com/Fission-AI/OpenSpec) 管理规格：

| 位置 | 含义 |
|---|---|
| `openspec/specs/<能力>/spec.md` | 系统**当前**的行为契约（Requirement + Scenario） |
| `openspec/changes/<变更>/` | 进行中的变更提案（proposal / specs 增量 / design / tasks） |
| `openspec/config.yaml` | 项目上下文与各工件的书写规则 |

```bash
npx @fission-ai/openspec@latest list             # 看有哪些进行中的变更与任务进度
npx @fission-ai/openspec@latest show <变更名>     # 看某个变更的详情
npx @fission-ai/openspec@latest validate --all   # 校验规格格式
npx @fission-ai/openspec@latest archive <变更名>  # 归档：把增量规格合并进主规格
```

**核心约定**：`specs` 描述系统**现在**是什么样，`changes` 描述**接下来**要改成什么样。
一次变更只做一件事，做完归档，规格随之增量增长——而不是维护一份越写越乱的大文档。

改代码前先看 `openspec/specs/` 里对应能力的契约；有行为变化就先提 change，
再按 `tasks.md` 逐项实现。


## 数据编排冒烟

一条命令用真 dbt 跑完整链路（建流程 → 编译 → 物化 → 注册数据集 → 验证行级权限与指标口径）：

```
cd backend && python scripts/flow_smoke.py --cleanup
```

跑三条流程、看四件事：

1. **权限链** —— 物化结果里不该出现越权仓的行；
2. dbt 日志里的表名应是 `dwd.xxx` 而非 `dwd_dwd.xxx`；
3. **口径链** —— 生成的 SQL 里应出现指标公式（`(amount - cost) / nullif(amount, 0)`），
   且算出来的毛利率与源数据吻合；
4. **质量链** —— 违规行被 `qa_passed=false` 标记且原因可读，**行数与上游一致**（不删行）。

指标公式来自 `dbt/models/marts/metrics.yml`，本机首次跑前先准备锚点数据集：

```
cd backend && python scripts/seed_metrics_demo.py
```

## 数据血缘

编排每次成功运行落一条 OpenLineage 事件（`lineage_events` 表；失败运行不记 ——
血缘只记"真的发生了的加工"）。数据集详情页可看**双向追溯**：

- **上游**：这批数从哪来 —— 逐层追到物理源表；
- **下游**：改它会砸到谁 —— 影响分析。

血缘只暴露名字与结构（无列清单、无数据），登录即可查 ——
影响分析必须能看到"别人的"下游，按权限裁剪会让结果静默漏掉看不见的节点。

**指标血缘**（`GET /api/v1/metrics/{name}/lineage`）把这条链延伸到口径：
一个指标被哪些流程引用 → 那些流程产出了什么 → 产物的下游是谁。
改公式之前先看这里，就知道该通知谁。引用关系从流程当前版本重算，不建物化表。

## 治理能力

中台能不能被运营起来，取决于这一层。

### 租户/组织隔离

数据集、用户、授权、流程都归属某个租户，跨租户互不可见。
存量数据全部归 `default` 租户，升级后行为与单租户时代一致。

- 隔离在**应用层**做（与既有的 `resolve_grant` 裁剪同一个思路），
  不用 Postgres RLS —— 本项目用连接池 + 后台任务，事务外 `SET` 不可靠；
- 普通管理员带 `X-Tenant-Id` 会 **403 而不是静默忽略**
  （静默忽略会让人以为切换成功了）；
- 跨租户取数返回 **404 而非 403**，不通过状态码差异泄露别人的数据集清单。

租户管理仅对**平台超管**（`is_superadmin`）开放，
与租户内管理员（`is_admin`）是两个正交权限。

### 权限有效期与申请审批流

授权可以设有效期（空 = 永久）。**过期即失效**，且**不删记录** ——
只把状态改成 `expired`，因为审计要能回答"这个人曾经有过什么权限"。

申请流：业务人员提交申请（理由必填）→ 管理员审批 → 通过时自动落成授权。

- **不能自批**自己的申请（否则申请流形同虚设）；
- 拒绝**必须填理由**；
- 管理员审批时可调整最终权限，校验规则与直建授权**完全相同**；
- 同一数据集同时只允许一条待审申请。

到期任务每小时把过期授权置 `expired`。注意：安全边界在**读时**的判定，
这个任务只是把事实写进状态，让界面能显示"已过期"。

### 数据质量巡检

与编排里的 `quality.assert` 算子是两件事：那是**单次运行的结果自检**，
这是**数据集级的运营期值守**。三类规则：

| 类型 | 回答的问题 | 阈值语义 |
|---|---|---|
| `freshness` | 这批数今天怎么没来 | 小时数 |
| `row_count` | 怎么突然少了一半 | 环比涨跌幅 % |
| `null_rate` | 这个字段怎么全是空 | 空值率 % |

- 巡检以规则 owner 的身份取数 —— 看到的数据范围与业务人员一致；
- 行数环比**首次巡检不判定**（无基线时报异常是误报）；
- 单条规则失败只落一条 `failed` 结果，**不炸调度线程**；
- 命中异常记 `quality.anomaly` 审计。

### 问数回答置信度

每个回答带 `high`/`medium`/`low` + **可解释的原因列表**（界面悬停可见）。

判定全部用**确定性信号**：证据行数、失败子问题数、SQL 改写次数、
是否命中指标字典、是否命中人工样例、自检结论。

**刻意不让模型自评** —— 它对"我不确定"没有可靠的自我认知，
而"自信地给出一个错的数"正是这么来的。自检结论只作**降档**信号：
模型说"够了"不作为升档依据，说"不够"是有价值的警告。

### 审计

除既有的取数/问数记录外，补齐合规审计最常查的三类事件：

| action | 记录内容 |
|---|---|
| `grant.create` / `grant.revoke` / `grant.approve` | 含行级规则快照与有效期，事后能还原"当时开的是什么权限" |
| `login.failed` | 用户名与失败原因，**绝不记密码** |
| `export.download` | 文件名、报表运行 id、行数 |
| `quality.anomaly` | 规则 id、实测值、基线、说明 |

审计写入失败**不影响**被审计的业务操作本身。

链路冒烟（用真 HTTP 打真服务，覆盖租户/质量/申请/血缘/审计）：

```
cd backend && python scripts/governance_smoke.py
```

OpenSpec change 结构自检（`npx openspec validate` 不可用时的替代）：

```
cd backend && python scripts/openspec_check.py
```

## 向量能力（AI 算子：文本向量化 / 语义去重）

`embed.text` 与 `dedup.semantic` 需要 embedding 环境，二选一：

1. **HTTP API**（立刻可用）：`backend/.env` 里设 `BISQL_EMBEDDING_API_BASE`
   （任何 OpenAI 兼容的 `/embeddings` 地址，可选 `BISQL_EMBEDDING_API_KEY` /
   `BISQL_EMBEDDING_MODEL`）；
2. **本地模型**（免费、数据不出机器）：`pip install "FlagEmbedding>=1.2"`
   （连带 torch），设 `BISQL_EMBEDDING_PROVIDER=local`；权重约 2.2GB，
   国内先把 `HF_ENDPOINT=https://hf-mirror.com`。

自检（不下载、不调用）：

```
cd backend && python scripts/check_embedding.py
```

链路冒烟（含 AI 算子的编排全链路，用本地假 embeddings 服务验证）：

```
cd backend && python scripts/embed_smoke.py --cleanup
```

**约定**：提供方不可用时系统会中止并说明两条出路，**绝不静默降级**成字符相似度 ——
降级产出的"分组"看起来像语义结果，实际不是。

## 模型配置（多厂商 API / 本地模型）

管理员在侧边栏「模型配置」里登记模型提供方，**立即生效、无需重启**：

- 用途：LLM（问数助手 / AI 算子）与 Embedding（向量化）各自设一个默认；
- 来源：厂商 API（DeepSeek / 通义 / 智谱 / Kimi / OpenAI / 硅基流动 / 自定义）
  或本地服务（Ollama / vLLM / LM Studio —— 都是 OpenAI 兼容协议）；
- 厂商模板自动填地址与常见模型；「测试连接」在保存前就能发现填错的地址；
  「拉取模型列表」从厂商拉真实可选清单；
- **API key 加密落库**（Fernet，主密钥从 `jwt_secret` 派生），界面只显示末 4 位，
  任何接口不回明文；编辑时留空 = 不修改。

优先级：**平台默认配置 > 环境变量**。什么都不配时行为与从前一致；
删除默认配置后自动回退环境变量。

链路冒烟（验证平台配置真实驱动模型调用、删除后回退）：

```
cd backend && python scripts/provider_smoke.py --cleanup
```

## 约定

- 新增查询路径一律用 `resolve_grant`（带管理员兜底），不要用 `get_effective_grant`，否则管理员会 403。
- 取数只走 `app/assistant/tools.py` 这一个入口，不要在别处手拼 SELECT。
- 物理列名保持 ASCII；中文展示名填数据集的字段注释（`datasets.column_comments`）。
- 新增模型必须配 alembic 迁移，并在 `app/models/__init__.py` 导出。
- **新增口径类字段（毛利率、动销率…）走 `dbt/models/marts/metrics.yml`，不要在流程里手写公式**：
  画布的「派生字段」填 `metric` 引用指标名，问数会拿到同一份口径，
  改口径只需改字典、下次编译生效。指标必须填 `owner`（这个数该问谁）。
  定义文件随代码版本化，改动走评审。
