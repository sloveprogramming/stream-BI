# Proposal: 模型提供方在平台内配置（多厂商 API / 本地模型）

## Why

现在换模型要**改 `.env` 并重启服务**，而且有三个实际问题：

- **API key 明文躺在配置文件里**。`.env` 会被备份、被同步、被截图进教程，
  key 一旦泄露就是按量计费的敞口；
- **厂商绑定在环境变量上**。想对比 DeepSeek 和通义的问答效果，得改文件、重启、再改回来；
  问数 Agent 和 AI 算子共用一套配置，想给它们用不同厂商也做不到；
- **「本地部署的模型」没有入口**。Ollama / vLLM / LM Studio 都是 OpenAI 兼容协议，
  明明一个 base_url 就能接，却要去翻文档找环境变量名。

这个需求对平台的定位（业务人员 + AI Agent 用的中台）是刚性：**模型是耗材，配置模型不该是一次运维动作。**

## What Changes

- 新增 `model_providers` 表：在平台里登记模型提供方（LLM / embedding 两类，
  API 或本地两类来源），**API key 加密落库**、接口返回永久脱敏；
- 新增**运行时配置解析器**：有平台默认配置就用它，没有则回退现有环境变量 ——
  不配置任何东西时系统行为与现在完全一致（零迁移成本）；
- 问数 Agent、AI 算子、向量化三处消费方全部改走解析器（改动各一处）；
- 新增管理员接口：增删改查、**测试连接**、**拉取厂商模型列表**、设默认；
- 新增前端管理页：厂商模板下拉（DeepSeek / 通义 / 智谱 / Kimi / OpenAI / 硅基流动 /
  Ollama / vLLM / 自定义）自动填地址与常见模型，填完可一键测连通。

## Capabilities

### New Capabilities

- `model-providers`：模型提供方的平台内配置、密钥保护、运行时生效与回退

### Modified Capabilities

- （无）既有 `data-flow` / `embedding-operators` 的契约不变：
  向量化"提供方不可用即中止"的语义原样保留，只是提供方多了一个来源。

## Impact

- 新增 `backend/app/models/provider.py` + alembic 迁移（本 change **新增表**）
- 新增 `backend/app/security/secrets.py`（Fernet 可逆加密，密钥从既有 `jwt_secret` 派生，
  **不新增依赖** —— `cryptography` 已在依赖里）
- 新增 `backend/app/ai/runtime.py`（运行时配置解析）
- 改动 `backend/app/assistant/llm.py`、`backend/app/ai/client.py`、`backend/app/ai/embedding.py`
- 新增 `backend/app/api/provider_api.py`（管理员接口）+ 挂载进 `main.py`
- 新增 `frontend/src/pages/admin/ModelProviders.tsx` + 菜单项
- **不新增运行时依赖**

## 不做什么（Out of Scope）

- **不做按用户配置**。内部平台、管理员统一配置；多租户是另一回事。
- **不做多协议抽象**。OpenAI 兼容协议已是事实标准（DeepSeek/通义/智谱/Ollama/vLLM 全兼容），
  只实现这一种；真出现第二种协议时再抽象。
- **不做 fallback 链**（主模型挂了自动切备用）。故障转移是可靠性工程，先不做。
- **不做用量统计与配额**（另行 change）。
- **不存密钥的明文副本**。加密用的主密钥来自 `jwt_secret`（HKDF 派生），
  与 JWT 同一安全边界 —— 换句话说，能读到数据库的人仍需要服务器上的 `jwt_secret` 才能解。
