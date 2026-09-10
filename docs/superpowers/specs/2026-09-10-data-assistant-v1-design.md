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
- JOIN 关系的人工配置界面（第一版由模型依据字段名 + 样例值推理，配置界面后续再加）；
- 会话历史持久化（第一版仅内存中当前会话）。
