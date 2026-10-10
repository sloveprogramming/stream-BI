# 权限与数据出口 Specification

## Purpose

定义 BIsql 的权限契约：**一套行级 + 字段级裁剪器，供所有数据出口复用**，
保证「同一问题、同一权限、同一结果」。

这不是一个附属功能，而是本系统区别于普通 BI / ETL 工具的核心：
权限不靠人守规范，而是由架构保证——任何新的取数路径都会被迫经过同一个裁剪器。

## Requirements

### Requirement: 统一裁剪口径

系统 SHALL 让所有数据读取路径共用同一个权限裁剪器（`app/access/`），
不得存在任何绕过它的取数路径。

#### Scenario: 多出口结果一致

- **GIVEN** 某用户对某数据集有 `warehouse = '德清'` 的行级规则
- **WHEN** 该用户分别通过 REST API、MCP 工具、自然语言问数、数据编排读取该数据集
- **THEN** 四次返回的行集合 MUST 完全一致

#### Scenario: 管理员无显式授权时仍可访问

- **GIVEN** 管理员对某数据集没有任何显式授权记录
- **WHEN** 管理员通过任一出口读取该数据集
- **THEN** 系统 MUST 放行
- **AND** MUST NOT 对其施加行级或字段级裁剪

#### Scenario: 可见列表与可查数据口径一致

- **GIVEN** 某用户仅通过用户直授（无角色）获得某数据集权限
- **WHEN** 该用户请求数据集列表
- **THEN** 该数据集 MUST 出现在列表中
- **AND** 列表口径 MUST 与「能否查到数据」的判定一致

### Requirement: 行级与字段级裁剪

系统 SHALL 在构造 SQL 时同时施加行级规则（注入 WHERE 条件）与字段白名单（列裁剪），
标识符经正则校验后拼接，值一律参数化。

#### Scenario: 行级规则限制可见行

- **GIVEN** 某用户对某数据集有 `warehouse = '德清'` 的行级规则，该表另有安徽的行
- **WHEN** 该用户查询该数据集
- **THEN** 返回结果 MUST 只包含德清的行

#### Scenario: 字段白名单同时挡住数据与列名

- **GIVEN** 某用户的字段白名单为 `["id", "region"]`，该表还有一列 `cost`
- **WHEN** 该用户查询该数据集
- **THEN** 返回的结果 MUST NOT 包含 `cost` 列
- **AND** 返回的列名清单 MUST NOT 包含 `cost`

#### Scenario: 非法标识符被拒绝

- **WHEN** 请求参数中出现不符合标识符规则的列名
- **THEN** 系统 MUST 拒绝该请求并返回可读错误
- **AND** MUST NOT 拼进 SQL 执行

### Requirement: 越权访问必须被拒绝

系统 SHALL 在用户对目标数据集无授权且非管理员时拒绝访问，
且不得泄露「该数据集是否存在」。

#### Scenario: 无授权的读取被拒

- **GIVEN** 某用户对目标数据集没有任何授权，且不是管理员
- **WHEN** 该用户请求该数据集的数据
- **THEN** 系统 MUST 返回 403 或 404
- **AND** MUST NOT 返回任何数据行

#### Scenario: 越权数据不得进入模型上下文

- **GIVEN** 某用户有 `warehouse = '德清'` 的行级规则
- **WHEN** 系统为该用户组装自然语言问数所需的 schema 上下文（含样例行）
- **THEN** 上下文中 MUST NOT 出现其他仓的真实值

### Requirement: 查询安全闸门

系统 SHALL 对模型生成的 SQL 施加硬约束：仅允许单条 SELECT、
所涉字段须在授权白名单内、只读事务、语句超时、返回行数上限。

#### Scenario: 非单条 SELECT 被拦截

- **WHEN** 待执行 SQL 不是单条 SELECT（例如含分号堆叠的语句、含 DDL/DML）
- **THEN** 系统 MUST 在闸门处拦截并返回可读错误
- **AND** MUST NOT 将其送入数据库执行

#### Scenario: 超时与行数上限生效

- **WHEN** 一次查询耗时超过语句超时，或返回行数超过上限
- **THEN** 系统 MUST 中止或截断，且不得影响中台的其他服务

### Requirement: 全景审计

系统 SHALL 记录每一次数据读取：主体、来源渠道、数据集、SQL 摘要、返回行数、耗时。

#### Scenario: 编排运行被纳入审计

- **WHEN** 一条数据编排流程运行
- **THEN** 审计记录 MUST 包含流程标识与运行标识
- **AND** 渠道字段 MUST 标记为编排来源

#### Scenario: 会话式问数可追溯

- **WHEN** 用户通过自然语言问数取数
- **THEN** 审计记录 MUST 能关联到具体会话与消息
- **AND** 记录 MUST 包含生成的 SQL 摘要
