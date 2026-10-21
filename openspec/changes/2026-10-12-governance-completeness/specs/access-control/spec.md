# access-control

## MODIFIED Requirements

### Requirement: 授权生效性判定

授权判定 SHALL 只认可**生效中**的授权：状态为 `active`、未设有效期或有效期未到。

用户直授优先于角色授权的顺序 SHALL 保持不变。

管理员兜底 SHALL 保持不变：管理员在无显式授权时获得全量权限。
管理员兜底 SHALL 同样受租户约束（不跨租户）。

授权判定 SHALL NOT 返回过期或已撤销的授权记录。

#### Scenario: 过期授权不参与判定

- **GIVEN** 用户有一条对某数据集的角色授权，但该授权已过期
- **WHEN** 判定该用户的权限
- **THEN** 判定结果不包含这条授权

#### Scenario: 过期授权不影响数据集可见性

- **GIVEN** 用户对某数据集的唯一一条授权已过期
- **WHEN** 查询该用户可见的数据集
- **THEN** 该数据集不出现

#### Scenario: 直授优先于角色授权仍然成立

- **GIVEN** 用户同时有直授与角色授权，且两条都生效
- **WHEN** 判定权限
- **THEN** 返回直授（行级规则与字段白名单以直授为准）

## ADDED Requirements

### Requirement: 审计事件覆盖授权变更、登录与导出

系统 SHALL 在审计日志中记录以下三类事件（合规审计最常查的事件）：

- 授权变更：`grant.create` / `grant.revoke` / `grant.approve`，
  detail SHALL 包含数据集 id、被授权对象、行级规则快照、字段白名单、有效期
  —— 事后要能还原"当时开的是什么权限"；
- 登录失败：`login.failed`，detail SHALL 包含用户名与失败原因；
  SHALL NOT 记录密码或任何凭据明文；
- 导出下载：`export.download`，detail SHALL 包含文件名、报表运行 id 与行数。

审计写入失败 SHALL NOT 影响被审计的业务操作本身。

#### Scenario: 授权创建留下规则快照

- **GIVEN** 管理员为某用户创建一条带行级规则与有效期的授权
- **WHEN** 操作完成
- **THEN** 审计中出现 `grant.create`，detail 含该规则与有效期

#### Scenario: 登录失败不记录密码

- **GIVEN** 一次使用错误密码的登录尝试
- **WHEN** 登录被拒绝
- **THEN** 审计中出现 `login.failed`，detail 含用户名与原因
- **AND** detail 中不含密码字段，也不含密码原文

#### Scenario: 用户不存在时也记录

- **GIVEN** 一次针对不存在用户名的登录尝试
- **WHEN** 登录被拒绝
- **THEN** 审计中出现 `login.failed`，且 `user_id` 为空
（失败时可能根本没有这个用户，不能靠外键反推）

#### Scenario: 导出下载被记录

- **GIVEN** 用户下载自己的报表文件
- **WHEN** 下载成功
- **THEN** 审计中出现 `export.download`，含文件名与行数

#### Scenario: 审计失败不阻塞业务

- **GIVEN** 审计写入因任何原因失败
- **WHEN** 执行被审计的操作
- **THEN** 操作本身仍然成功返回

### Requirement: 授权变更在界面上可追溯

管理界面的授权列表 SHALL 包含已过期与已撤销的授权，并标注其状态，
使管理员能看到"这个人曾经有过什么权限"。

查找重复授权时 SHALL 只考虑生效中的授权，使过期后可以重新授权同一对象。

#### Scenario: 已撤销授权仍在列表中

- **GIVEN** 一条被撤销的授权
- **WHEN** 管理员列出该数据集的授权
- **THEN** 该授权出现且状态标注为已撤销

#### Scenario: 过期后可为同一对象重新授权

- **GIVEN** 用户对某数据集的授权已过期
- **WHEN** 管理员再次为该用户创建该数据集的授权
- **THEN** 创建成功（不因存在历史授权而报冲突）
