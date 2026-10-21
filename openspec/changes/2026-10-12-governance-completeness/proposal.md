# Proposal: 数据中台治理补全（租户隔离 / 权限申请流 / 质量监控 / 指标血缘 / 回答置信度 / 审计补全）

## Why

权限与口径内核（一套裁剪器、五个出口复用、编排不可绕过权限）已经立住了，
但**外围治理缺一层，中台就立不住**：

1. **没有组织边界**：`User.is_admin` + `role_id` 是单租户模型。给别人用，
   第一件事就是租户隔离，否则 A 公司的数据集会出现在 B 公司的列表里。
2. **授权是管理员手工建、永久有效**：没有申请流，业务人员要等管理员记得；
   没有有效期，离职/转岗后权限静默残留 —— 这是审计最先查的东西。
3. **数据质量只有事后校验**：`quality.assert` 算子校验的是"这次跑出来的结果"，
   但没人回答"这批数今天怎么没来"（新鲜度）、"怎么突然少了一半"（行数波动）、
   "这个字段怎么全是空"（空值率）。中台最常被问的三个问题，现在都答不了。
4. **指标血缘没打通**：血缘只记编排产物。改一个指标口径会砸到哪些流程、
   哪些下游数据集、谁在看，现在只能靠人肉 grep。
5. **问数回答不可解释**：Agent 有 reflect，但用户看到一个数，
   分不清它是"SQL 直接算的"还是"模型推测的"。中台的数要能拿去做决策，必须可解释。
6. **审计看不全**：`AuditLog` 只记了 query/describe。**授权变更、登录失败、
   导出下载**这三样合规审计最常查的事件，一条都没记。

这六件事的共同点：都是"让一个中台能被运营起来"的那层，而不是又一个算子。

## What Changes

### 1. 租户/组织隔离（主流方案：行级隔离 + 默认租户）

采用市场主流做法（Postgres RLS 太重要级，Metabase/Directus/Grafana 均未采用）：

- 新增 `tenants` 表（id, name, slug, enabled）；
- `users` / `datasets` / `dataset_grants` / `data_flows` 等业务表加 `tenant_id`；
- **所有查询路径默认按当前用户租户过滤**（在 `get_any_principal` 统一注入，
  而不是在每个 API 里手写）—— 这是唯一可靠的接法，手写必然漏；
- **存量数据迁移到默认租户** `default`，行为与现在完全一致（零回归）；
- 管理员跨租户查询需显式 `X-Tenant-Id` 头（仅平台超管可用）。

### 2. 权限有效期与申请审批流

- `DatasetGrant` 增加 `expires_at`（空 = 永久）与 `status`（active/expired/revoked）；
  授权判定时**过期即失效**（不删数据，只改生效性）， expired 的授权在列表里可见但标灰；
- 新增 `grant_requests`（申请单）：申请人、数据集、申请理由、期望行级规则、
  期望字段白名单、有效期；状态机 `pending → approved/rejected/cancelled`；
- 审批通过**自动落成 `DatasetGrant`**（带有效期），拒绝需填理由；
- 申请人可撤销自己的 pending 申请；管理员可审批自己租户内的申请；
- 新增定时任务：到期授权自动置 expired 并记审计。

### 3. 数据质量主动监控（巡检，区别于 `quality.assert`）

新增 `data_quality_rules`（巡检规则）+ `data_quality_results`（巡检结果）：

- 规则类型（主流三件套，刻意只做这三个）：
  - `freshness`：源表最近更新时间距现在超过阈值即告警（"这批数今天怎么没来"）；
  - `row_count`：行数环比涨跌幅超阈值即告警（"怎么突然少了一半"）；
  - `null_rate`：指定字段空值率超阈值即告警（"这个字段怎么全是空"）。
- 巡检结果落库：value / threshold / status(ok/anomaly/failed) / message；
- 复用现有 APScheduler + `refresh_monitor_jobs` 同一套调度模式；
- **命中异常时不自动调 Agent**（与 `monitor` 的区别：那是归因，这是巡检，
  巡检要的是便宜且每天都能跑；归因留给 `monitor`）。

### 4. 指标血缘与影响分析

打通「指标 → 数据集 → 流程 → 下游」这条链：

- `metrics` 增加 `used_by`：哪些流程的 `field.derive` 引用了它（读时从
  `data_flow_versions` 重算，不新增表 —— 与 `lineage.py` 的"图可重算"思路一致）；
- 新增 `GET /api/v1/metrics/{name}/lineage`：返回该指标被哪些流程引用、
  这些流程的产物是什么、产物的下游是谁（复用 `lineage.downstream`）；
- 前端指标/数据集详情页展示"改这个口径会砸到谁"。

### 5. 问数回答置信度

给每个回答打上**可解释的置信度**，让用户知道这个数有多可信：

- `AnswerConfidence`：`high` / `medium` / `low` 三档 + `reasons`（机器可读原因列表）；
- 判定依据（确定性规则，不交给模型自评）：
  - `high`：SQL 直接执行成功、无重试、无 few-shot 兜底、指标命中字典；
  - `medium`：发生过 SQL 改写重试、或用了 few-shot 样例；
  - `low`：证据行数为 0、指标未命中字典（口径靠模型猜）、或 reflect 判定 evidence 不足；
- 落在 `agent_runs`（新增 `confidence` 列）并在回答里一并返回；
- 前端在回答卡片上展示置信度徽标 + 鼠标悬停显示 reasons。

### 6. 审计补全

补齐合规审计最常查的三类事件，**复用现有 `audit_logs` 表**（新增 action 取值，
不新建表 —— 审计要的是"一张表能查全"）：

- `grant.create` / `grant.delete` / `grant.expire` / `grant.revoke`
  （授权变更，detail 记录 数据集/对象/规则快照/有效期）；
- `login.failed`（登录失败，记录 username 与原因；不记密码）；
- `export.download`（导出文件被下载，记录文件名与行数）。

## Capabilities

### New Capabilities

- `multi-tenancy`: 租户定义与跨所有出口的隔离
- `grant-lifecycle`: 权限申请、审批、有效期与自动过期
- `data-quality`: 数据质量巡检（新鲜度 / 行数 / 空值率）
- `metric-lineage`: 指标血缘与影响分析
- `answer-confidence`: 问数回答置信度与可解释性

### Modified Capabilities

- `access-control`: 授权判定增加有效期与状态；审计事件类型扩充
- `data-flow`: 指标被流程引用的事实可被追溯
- `assistant-agent`: 回答附带置信度

## Impact

**新增**：

- `backend/app/models/tenant.py`（Tenant + 各表 tenant_id）
- `backend/app/models/grant_request.py`（GrantRequest）
- `backend/app/models/quality.py`（DataQualityRule / DataQualityResult）
- `backend/app/tenancy/`（租户上下文注入与过滤）
- `backend/app/grants/`（申请流状态机 + 有效期判定）
- `backend/app/quality/`（巡检执行器）
- `backend/app/metrics/lineage.py`（指标血缘）
- `backend/app/assistant/confidence.py`（置信度判定）
- 对应 `alembic/versions/*` 迁移
- 前端页面：租户管理、权限申请/审批、质量巡检看板、指标血缘、回答置信度徽标

**改动**：

- `app/models/access.py`：`DatasetGrant` 加 `expires_at` / `status`
- `app/access/grants.py`：授权判定过滤过期项；所有查询加租户过滤
- `app/api/deps.py`：`get_any_principal` 注入租户上下文
- `app/api/admin_api.py`：授权创建支持有效期；新增申请/审批端点
- `app/api/auth_api.py`：登录失败记审计
- `app/api/export_api.py`：下载记审计
- `app/main.py`：挂载新路由 + 质量巡检定时任务
- `app/models/__init__.py`：导出新模型

**不新增运行时依赖**：全部用现有 SQLAlchemy / FastAPI / APScheduler。

## 不做什么（Out of Scope）

- **不做 Postgres RLS（行级安全策略）**。RLL 需要为每个连接 SET 租户上下文，
  而本项目用连接池 + APScheduler 后台任务，事务外设置不可靠；
  且本项目已有成熟的 `resolve_grant` 裁剪层，在应用层过滤与既有架构一致、
  可测试、可单测。RLS 留作将来多租户强隔离的备选。
- **不做租户自助注册/付费**。租户由平台超管创建，本 change 只做隔离。
- **不做质量规则的 UI 编辑器拖拽**。规则用表单创建即可。
- **不替换 `quality.assert` 算子**。那是编排内的行级校验，本 change 是数据集级巡检，
  两者互补，不合并。
- **不做指标的可视化血缘大图**。先用分层列表（与现有 `lineage` 接口一致）。
- **不做细粒度的回答置信度模型**（如让 LLM 自评）。刻意用确定性规则，
  可解释、可单测；模型自评正是"自信地给出一个错的数"的来源。
