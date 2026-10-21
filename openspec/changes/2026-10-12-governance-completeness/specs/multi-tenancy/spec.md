# multi-tenancy

## ADDED Requirements

### Requirement: 租户定义与归属

系统 SHALL 支持多租户（组织），每个租户有唯一的 `slug` 用于接口标识。

用户、数据集、授权、流程、报表任务、监控任务 SHALL 归属某个租户。
归属信息在创建时写入，允许为空（视为 `default` 租户）。

系统 SHALL 在升级既有部署时，把全部存量数据归入 `slug='default'` 的租户，
使升级后的可见性与升级前**完全一致**。

#### Scenario: 存量数据升级后行为不变

- **GIVEN** 一个单租户时代的部署（数据集、用户、授权均无租户字段）
- **WHEN** 执行迁移
- **THEN** 全部记录的 `tenant_id` 都指向 `default` 租户
- **AND** 任一用户可见的数据集集合与升级前相同

#### Scenario: 新建记录自动带上租户

- **GIVEN** 管理员 A 属于租户 T
- **WHEN** A 通过接口创建数据集或用户
- **THEN** 新建记录的 `tenant_id` 为 T
- **AND** 该记录在按租户查询时对 T 可见

### Requirement: 查询按租户隔离

系统 SHALL 在所有数据集可见性查询路径上按租户过滤：
REST 列表、数据集取数、导出、MCP 工具、数据助手 schema 上下文、报表。

管理员 SHALL 只看到**本租户内**的全部数据集，而非全库。

请求数据集时，若数据集属于其他租户，系统 SHALL 返回 404 而非 403 ——
不通过状态码差异泄露"其他租户存在哪些数据集"。

#### Scenario: 跨租户数据集不可见

- **GIVEN** 租户 T1 的用户 U 与租户 T2 的数据集 D
- **WHEN** U 请求 `/datasets` 或按名字请求 D 的行数据
- **THEN** D 不出现在列表中，且按名字请求返回 404

#### Scenario: 跨租户授权被拒绝

- **GIVEN** 租户 T1 的用户 U 与租户 T2 的数据集 D
- **AND** 数据库中存在一条 U 对 D 的授权记录（异常数据）
- **WHEN** 判定 U 对 D 的权限
- **THEN** 判定结果为无权限（纵深防御，不只依赖查询过滤）

#### Scenario: 非超管不能切换租户

- **GIVEN** 租户内管理员 A（`is_admin=true`，`is_superadmin=false`）
- **WHEN** A 的请求带 `X-Tenant-Id` 头
- **THEN** 系统返回 403，且**不静默忽略**该头

### Requirement: 租户管理仅限平台超管

系统 SHALL 区分两个正交权限：`is_admin` 管理租户内事务，
`is_superadmin` 管理跨租户事务。

创建租户、停用租户、跨租户切换 SHALL 仅对 `is_superadmin` 开放。

`default` 租户 SHALL NOT 被停用。

#### Scenario: 普通管理员不能建租户

- **GIVEN** `is_admin=true` 且 `is_superadmin=false` 的用户
- **WHEN** 该用户调用创建租户接口
- **THEN** 返回 403

#### Scenario: 停用的租户立即使其成员失效

- **GIVEN** 租户 T 已被停用，用户 U 属于 T
- **WHEN** U 发起任何需要鉴权的请求
- **THEN** 返回 403 并说明租户已停用
