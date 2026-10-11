# Design

## Context

**现状**：模型配置全在 `app/settings.py`（`BISQL_LLM_*` / `BISQL_EMBEDDING_*`），
消费方三处：`assistant/llm.py::build_client / model_for`（问数与 Agent）、
`ai/client.py::structured_call`（AI 算子，经 `chat()` 间接走前者）、
`ai/embedding.py::resolve_status / _embed_via_api`（向量化）。

**既有约定**（`openspec/config.yaml`）：只加不换、不自研格式、不引中间件、
改模型必须配迁移、`resolve_grant`、取数只走 `assistant/tools.py`。

**复用的现有模块**：

- `app/api/admin_api.py::_require_admin` —— 管理员门槛的既有写法
- `app/security/`（`apikeys.py` / `jwts.py`）—— 安全工具的家，新加密放这里
- `app/ai/embedding.py::resolve_status / _embed_via_api` —— 提供方探测的骨架，
  DB 配置作为**更高优先级的一个来源**接进去
- `app/assistant/llm.py::build_client / build_planner_client / model_for` ——
  签名与行为不变，只换"配置从哪来"

## Goals / Non-Goals

**Goals:**

- 平台内配置、即时生效（不用重启）；不配置时行为与现在**完全一致**
- key 加密落库、接口永不回显明文
- 「测试连接」能在保存前发现填错的地址

**Non-Goals:**

- 多用户配置、多协议、fallback 链、用量统计（见 proposal 的 Out of Scope）

## Decisions

### 决策 1：运行时解析器，DB 覆盖 env —— 不做"启动时读库写入 settings"

`app/ai/runtime.py` 提供 `resolve_llm() / resolve_embedding()`：
查 `model_providers` 里启用的默认配置 → 有就用；没有 → 回退 settings。
消费方每次调用时解析。

**为什么不做"启动时覆盖 settings"**：那样改配置仍要重启，需求就白做了；
而且 `get_settings()` 是 `lru_cache` 全局单例，就地改写它的字段是全局可变状态，
两个请求可能用到不同版本 —— 运行时解析反而更干净。

**代价**：每次模型调用多一次主键级查询。本机单机、调用本身是秒级的 LLM 往返，
这点开销可以忽略；真要省，将来加个 5 秒 TTL 缓存即可，接口不变。

### 决策 2：key 用 Fernet 加密，主密钥从 `jwt_secret` 派生

`app/security/secrets.py`：`encrypt_secret / decrypt_secret / mask_secret`。
派生方式取 SHA-256(jwt_secret) 做 Fernet key（同一安全边界：拿到库的人
还需要服务器上的 `jwt_secret` 才能解，与 JWT 的防护等价）。

**为什么不引 KMS / 环境注入**：单机部署、key 的威胁模型是"配置文件被随手泄露"，
Fernet 已经把明文从 `.env` 和数据库里都拿掉了。`jwt_secret` 更换时旧 key 解不开 ——
错误信息必须直接说清这个原因（见 Risks）。

### 决策 3：只支持 OpenAI 兼容协议，本地服务也是它

DeepSeek / 通义 / 智谱 / Kimi / OpenAI / 硅基流动 / **Ollama / vLLM / LM Studio**
全都提供 OpenAI 兼容端点。所以数据模型里**不需要** protocol 字段：
`provider_kind = api | local` 加 `base_url` 就能覆盖全部现实场景
（本地 = 地址指向本机 + key 可空）。

将来真出现第二种协议再加字段 —— 现在加是死字段。

### 决策 4：测试连接与拉模型列表分开

- `POST /admin/providers/test`：body 带临时参数（**保存前**测表单）
- `POST /admin/providers/{id}/test`：用已存配置（**编辑后**回归验证）
- `POST /admin/providers/models` / `POST /admin/providers/{id}/models`：拉 `/v1/models`

测试手段按 kind 区分：LLM 调 `models.list()`（免费、所有兼容服务都实现）；
embedding+api 发一条 `"ping"` 的真实向量化（一次调用成本可忽略）；
embedding+local 只做依赖 import 探测（不加载权重）。

### 决策 5：同 kind 唯一默认，用"设默认时清同侧行"实现

不做部分唯一索引（PostgreSQL 支持但 alembic 表达繁琐），应用层在
`set_default` 事务里把同 kind 的其他行清掉。并发下最多出现两个默认、
解析器取 id 更小的一条 —— 单管理员场景无实际风险，不值得上数据库约束。

### 决策 6：LLM 一条配置带可选的 planner_model

既有行为是 SQL 生成用快模型、规划反思用推理模型。数据模型用一条配置
装下两者（`model` + `planner_model` 可空），而不是拆两条 ——
前端少一个 Tab，"换厂商"永远是一处操作。

## Risks / Trade-offs

- **每次调用查一次库**（决策 1）。可忽略；将来 TTL 缓存接口不变。
- **`jwt_secret` 更换导致旧 key 解密失败**。解密失败时**不抛堆栈**，
  而是报"主密钥已更换，请重新填写该配置的 API key"—— 这是可操作的失败。
- **embedding 的 `resolve_status` 分支变多**（DB → env 原逻辑）。
  应对：DB 分支复用同一个 `ProviderStatus` 结构与"不可用即中止"语义，
  `embedding-operators` 的契约原样成立；用单测锁住优先级。
- **前端厂商模板的地址会过期**（厂商改域名）。模板只是"自动填"的快捷方式，
  全部可手改；这本来就是表单字段，不是硬编码。
