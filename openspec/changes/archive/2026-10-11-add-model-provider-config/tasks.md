# Tasks: 模型提供方平台内配置

## 1. 数据与安全

- [x] 1.1 `backend/app/models/provider.py`：`ModelProvider` 表
  （name / kind / provider_kind / base_url / encrypted_api_key / model / planner_model /
  enabled / is_default），加入 `models/__init__.py` 导出
  验收：alembic 迁移 `e2f8a3b6c1d7` 可升降级；`alembic check` 无差异
  —— 过程中抓到一处：迁移里建了复合索引但模型没声明，check 报 remove_index，
  在模型 `__table_args__` 补声明后归零（模型与迁移必须双向一致）

- [x] 1.2 `backend/app/security/secrets.py`：`encrypt_secret` / `decrypt_secret` / `mask_secret`
  （Fernet，主密钥从 `jwt_secret` 派生；**不新增依赖**，`cryptography` 本就在依赖里）
  验收：单测断言加密往返一致、密文不含明文、掩码只露前 3 + 末 4、
  **换主密钥后解密报可操作的「请重新填写」而不是堆栈**

## 2. 运行时解析器与消费方改造

- [x] 2.1 `backend/app/ai/runtime.py`：`resolve_llm()` / `resolve_embedding_api()` /
  `default_provider()`（DB 默认优先，未配置回退 env；`session` 可注入便于测试）
  验收：单测断言 —— 无配置回退 env、DB 默认胜出（source="db:<名>"，key 解密正确）、
  **删除默认后回退 env**、**停用的默认被忽略**、local 来源 key 为空、
  planner_model 留空回退到 model

- [x] 2.2 改造 `assistant/llm.py`（`build_client` / `build_planner_client` / `model_for`
  走解析器，**签名与行为不变**）
  验收：冒烟断言 chat 请求发往 DB 配置的地址、`model_for("sql")` 取到 DB 配置的模型

- [x] 2.3 改造 `ai/client.py::ensure_llm_ready`（就绪判定认运行时配置；
  平台配了默认提供方即使 key 为空（本地服务）也算就绪）
  验收：错误信息同时指向「平台模型配置页」与「环境变量」两条路

- [x] 2.4 改造 `ai/embedding.py::resolve_status`（DB 默认作为最高优先级来源，
  api / local 沿用既有语义：不可用即中止、**不回退**）
  验收：单测断言「DB api 行生效（configured="db"）」「DB local 缺依赖时报错且不回退 env」

## 3. 管理接口

- [x] 3.1 `backend/app/api/provider_api.py`：列表（key 脱敏）/ 新建（每用途第一条自动设默认）/
  编辑（**api_key 键不存在 = 不修改，空串 = 清空**）/ 删除 / 设默认（同 kind 唯一 +
  顺手启用）/ 测试连接（临时参数 + 已存配置）/ 拉模型列表；挂载进 `main.py`；全部 admin only
  验收：单测 10 例 —— **创建/列表/更新响应全文不含明文 key**、库里是密文、
  设默认让位、非管理员所有端点 403、连不上返回人话（还发现走代理环境下
  连接错误表现为 5xx 而非 APIConnectionError，已分类）、models 不支持时给 hint

## 4. 前端

- [x] 4.1 `frontend/src/pages/admin/ModelProviders.tsx` + 路由 + 管理员菜单项：
  列表 / 新建编辑弹窗（厂商模板下拉自动填地址与常见模型、测试连接、拉取模型列表、
  key 掩码显示、设默认、启停 Switch）；模型选择用 AutoComplete（可手填也可从清单选）
  验收：`tsc -b && vite build` 通过（1.83s）
  —— 踩坑：`tsc` 的 `noUnusedLocals` 又拦了一个没用到的 icon import

## 5. 验证与收尾

- [x] 5.1 全量 `python -m pytest -q` + `alembic check`
  验收：**438 passed**（改动前 412，+26）；`No new upgrade operations detected.`

- [x] 5.2 端到端冒烟 `backend/scripts/provider_smoke.py`：
  本地假 OpenAI 兼容服务（chat / embeddings / models 三端点）→
  走**真实 HTTP 管理接口**登记为默认（key 加密落库）→
  发一次真实 chat 与一次真实向量化
  验收：实测 `POST /v1/chat/completions` 与 `POST /v1/embeddings` 都打到了登记的地址
  （chat 返回假服务的应答、embedding 维度 3）；**删除配置后 `resolve_llm().source == "env"`**

- [x] 5.3 `README.md` 补「模型配置」一节（入口、厂商模板、key 安全、优先级与回退）
  验收：README 里能找到

- [x] 5.4 `openspec validate --all` 全过后归档
  验收：`summary.failed` 为 0
