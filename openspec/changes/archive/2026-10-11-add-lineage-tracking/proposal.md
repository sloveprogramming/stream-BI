# Proposal: 数据血缘（OpenLineage 事件 + 双向追溯查询）

## Why

编排跑起来之后，"这批数怎么来的"散落在三张表里：
`data_flow_versions.input_datasets`（流程输入）、`data_flows.target_table`（产物）、
`datasets.source_table`（物理源）。**数据都在，没人能回答两个最常被问的问题**：

- **影响分析**：「我要改/停这个数据集，会砸到哪些下游产物？」—— 现在只能人肉翻流程列表；
- **来源追溯**：「报表上这个数不对，它上游经过哪些加工？」—— 排障时只能猜。

这正是数据中台与裸数据库的分界线之一：**血缘不是文档，是可查询的结构**。

技术选型沿用 v2 方案的既定决策：**OpenLineage 事件格式落自建 `lineage_events` 表，
不部署 Marquez / DataHub**（单机平台不背图数据库的运维）——
事件是标准格式（将来接任何血缘工具都认），查询在本库内做。

## What Changes

- 新增 `lineage_events` 表：每次编排**成功**后落一条 OpenLineage `RunEvent`
  （job = 流程、run = 本次运行、inputs = 输入数据集、outputs = 产物），payload 全量存 JSONB；
- 新增血缘查询服务：基于「流程输入 → 产物」与「数据集 → 物理源表」两类边，
  提供**上游 N 层**与**下游 N 层**的双向追溯（防环、防重复）；
- 新增查询 API（登录即可查，只暴露名字与结构，不含数据）；
- 前端数据集详情页新增「血缘」区块：分层列出上游/下游；
- 冒烟加验证：跑完权限链后，产物的上游含源数据集、源数据集的下游含产物。

## Capabilities

### New Capabilities

- `lineage`：编排血缘事件的落库与双向追溯查询

### Modified Capabilities

- （无）`data-flow` 既有契约不变：发事件是新增动作，不改变运行语义。

## Impact

- 新增 `backend/app/models/lineage.py` + alembic 迁移（**新增表**）
- 新增 `backend/app/flow/lineage.py`（事件构造 + 双向追溯）
- 改动 `backend/app/flow/runner.py`（成功路径 emit 事件）
- 新增 `backend/app/api/lineage_api.py` + 挂载
- 改动 `frontend/src/pages/DatasetQuery.tsx`（血缘区块）
- 改动 `backend/scripts/flow_smoke.py`（血缘断言）

## 不做什么（Out of Scope）

- **不部署 Marquez / DataHub**（既定决策，单机不背图库运维）
- **不做问数/MCP 出口的血缘**：血缘 MVP 只覆盖"编排"这一条生产路径；
  查询是消费不是生产，不加边
- **不做 AI 中间表的血缘边**：`ai_tmp` 的物化表是流程内部的实现细节，
  业务血缘只到"流程"这一层
- **不做血缘认证/签名**：事件是本系统自己产生的，不存在伪造来源问题
- **前端不做交互式 DAG 图**（React Flow 复用看着诱人）：血缘通常 1~2 层深，
  分层列表信息密度更高且便宜得多；真出现深层网状血缘再上图
