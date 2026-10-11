# Spec Delta

## Purpose

把「模型用谁家、key 是什么」从运维动作（改 .env + 重启）变成平台内的一次配置，
让业务人员和开发都能在界面里切换厂商或接本地模型，同时 key 不再明文散落。

## ADDED Requirements

### Requirement: 平台内登记模型提供方

系统 SHALL 允许管理员在平台内登记模型提供方，每条配置 MUST 包含
名称、用途（LLM / embedding）、来源（API / 本地）、API 地址、默认模型；
LLM 配置 MAY 另含规划用途的模型名。

#### Scenario: 登记一条厂商 API

- **WHEN** 管理员登记一条 DeepSeek 的 LLM 配置（地址、key、模型名）
- **WHEN** 后再次查看配置列表
- **THEN** 该配置 MUST 出现且字段完整
- **AND** key MUST 以脱敏形式展示

#### Scenario: 登记一个本地模型

- **GIVEN** 本机跑着 Ollama 等兼容服务
- **WHEN** 管理员登记来源为「本地」、地址指向本机服务的配置
- **THEN** 系统 MUST 接受该配置并按同样方式生效
- **AND** key MUST 允许为空（本地服务通常不校验）

### Requirement: API key 加密落库且接口脱敏

系统 SHALL 对 API key 做可逆加密后落库；任何接口响应 MUST NOT 包含明文 key。

#### Scenario: 数据库里读不到明文

- **GIVEN** 管理员登记了一条带 key 的配置
- **WHEN** 直接查看数据库中该行
- **THEN** key 字段 MUST NOT 是明文
- **AND** 系统内部调用时 MUST 能解密出原文

#### Scenario: 接口响应永不回显明文

- **WHEN** 查询配置列表或详情
- **THEN** 响应中的 key MUST 是掩码（如 `sk-…abcd`）
- **AND** 编辑时 key 留空 MUST 表示「不修改」

### Requirement: 运行时生效与回退

系统 SHALL 让「平台默认配置」在运行时生效；未配置或默认配置不可用时，
MUST 回退到环境变量配置，且行为与既有版本一致。

#### Scenario: 配置了默认提供方后调用走它

- **GIVEN** 已登记一条启用的 LLM 默认配置
- **WHEN** 问数或 AI 算子发起模型调用
- **THEN** 请求 MUST 发往该配置的 API 地址并使用其模型名
- **AND** MUST NOT 使用环境变量里的旧配置

#### Scenario: 未配置时回退环境变量

- **GIVEN** 平台内没有登记任何（或没有启用的）该类默认配置
- **WHEN** 系统发起模型调用
- **THEN** MUST 使用环境变量配置，行为与既有版本一致

#### Scenario: 删除默认配置后回退

- **GIVEN** 一条默认配置存在且生效
- **WHEN** 管理员删除它
- **THEN** 后续调用 MUST 回退到环境变量配置
- **AND** MUST NOT 报"找不到配置"

#### Scenario: embedding 的本地提供方

- **GIVEN** 已登记来源为「本地」的 embedding 默认配置
- **WHEN** 流程执行向量化算子
- **THEN** 系统 MUST 使用本地模型路径
- **AND** 本机缺少模型依赖时 MUST 按「提供方不可用」中止（沿用既有语义）

### Requirement: 测试连接

系统 SHALL 提供连通性测试：既支持测试**尚未保存**的参数，也支持测试**已保存**的配置；
失败时 MUST 给出可定位的原因。

#### Scenario: 保存前测试

- **WHEN** 管理员在表单里填写参数并点击测试
- **THEN** 系统 MUST 用这些参数发起一次真实调用
- **AND** 成功时返回模型可达信息，失败时返回具体原因（网络 / 鉴权 / 模型名）

#### Scenario: 已保存配置测试

- **WHEN** 管理员对已保存的配置点击测试
- **THEN** 系统 MUST 用**存储的解密后**参数发起调用
- **AND** 响应 MUST NOT 包含 key

### Requirement: 拉取厂商模型列表

系统 SHALL 能用一条配置（或表单参数）调用厂商的模型清单接口，
供配置时从真实可选列表里选择模型。

#### Scenario: 拉取模型列表

- **WHEN** 管理员点击「拉取模型列表」
- **THEN** 系统 MUST 调用该提供方的模型清单接口
- **AND** 返回模型 id 列表；接口不支持时 MUST 给出明确提示而非静默空列表

### Requirement: 默认配置唯一

系统 SHALL 保证同一用途（LLM / embedding）同时最多只有一条默认配置。

#### Scenario: 设为默认时自动让位

- **GIVEN** LLM 用途下已有一条默认配置
- **WHEN** 管理员把另一条 LLM 配置设为默认
- **THEN** 原默认 MUST 自动取消默认标记
- **AND** 新配置 MUST 生效

### Requirement: 仅管理员可管理

系统 SHALL 限制模型配置的增删改查与测试为管理员；其他用户 MUST 被拒绝。

#### Scenario: 非管理员访问被拒

- **WHEN** 非管理员用户请求模型配置的任意管理接口
- **THEN** 系统 MUST 返回 403
