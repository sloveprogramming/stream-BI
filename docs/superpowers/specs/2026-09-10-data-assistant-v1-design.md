# 数据助手（ChatBI）第一版设计文档

日期：2026-09-10
状态：待用户评审

## 1. 目标

在 BIsql 数据分发中台左侧菜单新增「数据助手」：用户用自然语言提问，助手把问题转换为只读 SQL 并返回表格结果，支持跨数据集 JOIN（例如订单表与库存表按商品 ID 联表）。结果必须严格套用用户现有的数据权限（行级规则 + 字段白名单），并支持一键导出。

第一版范围（“实用版”）：

- 一问一查：单条自然语言问题 → 一条 SQL → 表格结果；
- 不做多步分析、自动报告、图表（后续“分析版”再做）。

## 2. 技术方案

### 2.1 总体架构

在现有 FastAPI 单体上新增 `assistant/` 后端模块和前端「数据助手」页面。大模型使用 DeepSeek（OpenAI 兼容接口，模型 `deepseek-chat`）。

参考项目 `E:\WorkTool\agent_class\ChatBi`（本地 ChatBI 教学项目）提供架构思路：schema linking、join 推理、安全拦截。我们只借鉴思路，不整体搬运其 MySQL / ChromaDB 依赖。

链路：

```
用户提问
  → 后端取该用户有权限的数据集结构（含样例值）
  → 构建 Prompt 调用 DeepSeek 生成 SQL
  → SQL 安全闸门（只读校验 + 表/字段权限校验）
  → 权限重写（表引用替换为行级过滤子查询）
  → 只读事务执行
  → 返回：结果表格 + 生成 SQL + 导出链接
```

### 2.2 后端模块

文件均位于 `backend/app/`：

| 文件 | 职责 |
|---|---|
| `assistant/llm.py` | DeepSeek 客户端封装（OpenAI 兼容，非流式调用 + 超时 + 重试） |
| `assistant/schema_context.py` | 根据用户有效授权收集数据集结构：表名、字段、类型、样例值；生成 Prompt |
| `assistant/sql_guard.py` | 只读 SQL 校验 + 表/字段权限校验 + 危险内容拦截 |
| `assistant/sql_rewrite.py` | 用 SQL 解析库把表引用替换为带行级规则的子查询 |
| `assistant/chat_api.py` | `POST /api/v1/assistant/chat`，SSE 流式响应 |
| `assistant/__init__.py` | 导出 |

新依赖：`sqlglot`（SQL 解析与重写）、`openai`（调用 DeepSeek 兼容接口）。

### 2.3 权限模型（关键设计）

1. **数据集可见性**：只有用户通过角色授权或账号直授获得的数据集才会进入 Prompt，DeepSeek 看不到其他表。
2. **字段级**：白名单之外的字段不可出现在 SQL 中；违规返回 403 提示。
3. **行级**：执行前将 SQL 中每个表引用替换为子查询 `(SELECT 白名单字段 FROM 表 WHERE 行级规则)`，再执行 JOIN。行级过滤先于 JOIN，保证跨表联表不漏权限。
4. **只读**：`sql_guard` 拒绝非 SELECT/WITH-SELECT 语句、多条语句、分号注入、危险关键字；执行使用只读事务并设置语句超时与行数上限。

用户直授优先级高于角色授权（复用 `get_effective_grant`）。

### 2.4 API 契约

`POST /api/v1/assistant/chat`

- 鉴权：JWT 或 API Key（复用 `get_any_principal`）。
- 请求：`{"question": "把订单和库存按商品ID合并，看各商品的销量和库存"}`
- 响应：SSE 事件流
  - `{"type":"sql","sql":"SELECT ..."}`
  - `{"type":"result","columns":[...],"rows":[...],"row_count":N}`
  - `{"type":"error","message":"..."}`
  - `{"type":"done"}`

SSE 只用于向前端推送响应事件，DeepSeek 本身用非流式调用。导出由前端根据返回结果在前端生成 CSV/XLSX 文件（返回行数受 `BISQL_ASSISTANT_MAX_ROWS` 上限约束，不会产生超大结果）。

### 2.5 前端

- 左侧菜单新增「数据助手」；
- 页面：消息列表 + 输入框；助手回复渲染：结果表格（分页）、生成 SQL 可折叠查看、导出按钮、错误中文提示；
- SSE 流式渲染进度提示。

### 2.6 配置

`backend/.env` 增加：

```env
BISQL_LLM_API_KEY=<DeepSeek key>
BISQL_LLM_BASE_URL=https://api.deepseek.com
BISQL_LLM_MODEL=deepseek-chat
BISQL_LLM_TIMEOUT_SECONDS=60
BISQL_ASSISTANT_MAX_ROWS=1000
```

密钥只走环境变量，不入库不入 git。

## 3. 错误处理

- DeepSeek 超时/网络错误 → 返回“模型暂时不可用，请稍后重试”；
- SQL 非只读 / 越权字段 → 403 中文提示；
- SQL 语法错误 → 展示“查询解析失败”并附带可读说明；
- 空结果 → 正常返回空表格；
- 查询超时/超大结果 → 限制行数与执行时间，返回提示。

## 4. 测试

- 单元：`sql_guard`（拒绝 insert/update/delete/多语句/越权字段；放行合法 SELECT）、`sql_rewrite`（表引用重写 + 行级规则注入）、`schema_context`（只收集授权数据集）、`llm`（mock 响应）；
- 集成：聊天接口用假模型返回固定 SQL，走真实权限与 PostgreSQL，验证行级/字段级裁剪；
- 端到端：真 DeepSeek 冒烟（手动，可选）。

## 5. 不在第一版范围

- 多步分析、自动分析报告、图表；
- JOIN 关系的人工配置界面（第一版由模型依据字段名 + 样例值推理，配置界面后续再加）。

## 6. v1.1 增量（2026-09-10 追加）

- **会话 ID 与聊天记录**：前端 `DataAssistant.tsx` 支持多会话（新建/切换/删除），会话与消息持久化在浏览器 localStorage（`assistant_sessions_v1`），标题取首个提问；卡片标题展示可复制的前 8 位会话 ID。请求体新增 `session_id`，后端写入 `audit_logs`（`channel="assistant"`，`detail` 含 `session_id/question/sql/error`），可按会话追溯问答。持久化时每会话最多保留 20 行结果，避免超出 localStorage 配额。
- **数据集准实时可见**：`app/sync/discovery.py` 定时（默认 10s）扫描 `ods` 模式，把 ERP 同步新建的表自动注册为数据集；前端数据集列表与详情页按 5s 轮询刷新，新数据集会在列表打「新」角标。配置见 `BISQL_DISCOVERY_*`。
- **可见性**：自动发现的数据集默认**仅管理员可见**（管理员无授权即可查询，走 `AdminGrant`），普通用户仍需管理员在「数据集与授权」页显式授权。
- **生成接口调用方案**：`app/assistant/api_recipe.py` 把生成的 SQL 反解成可直接调用的接口方案，随 `{"type":"api"}` SSE 事件返回，前端以「生成接口（可直接调用）」折叠面板展示 cURL / Python / JavaScript / MCP 四种示例（一键复制）+ 注意事项。
  - 只对**能一一对应**的查询给方案：单表、无 JOIN/子查询/UNION/GROUP BY/聚合/DISTINCT，且 WHERE 全部为等值 AND 条件；此时等价于 `GET /api/v1/datasets/{name}/rows?字段=值&limit=&offset=`（等价于 MCP `query_dataset`）。
  - 其余情况返回 `callable=false` + 中文原因（如多表联查、聚合统计、TopN 排序截断、范围/模糊筛选），避免给出与本次结果不一致的调用方式。
  - 边界处理：无 LIMIT 时按接口上限 500 收敛并提示分页；LIMIT 超上限则收敛并提示；按非主键字段 ORDER BY + LIMIT（TopN）判定不可对应；ORDER BY 非主键但无 LIMIT 时保留并提示顺序差异。
