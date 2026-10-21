# grant-lifecycle

## ADDED Requirements

### Requirement: 授权有效期

每条数据集授权 SHALL 可以设置有效期（`expires_at`），未设置表示永久有效。

授权判定 SHALL 同时满足三个条件才算生效：状态为 `active`、未设有效期或
有效期未到、行级规则存在。

**过期即失效**：到达 `expires_at` 的那一刻起授权不再生效，不留时间窗口。

过期的授权 SHALL NOT 被删除：系统只把状态改为 `expired`，历史授权记录
必须保留以便审计追溯"这个人曾经有过什么权限"。

#### Scenario: 过期授权不再生效

- **GIVEN** 一条 `status='active'` 且 `expires_at` 已过去的授权
- **WHEN** 持有者查询对应数据集
- **THEN** 系统判定其无权限（403）

#### Scenario: 到期任务把状态写实

- **GIVEN** 一条已过期的 `active` 授权
- **WHEN** 到期任务运行
- **THEN** 该授权的状态变为 `expired`
- **AND** 再次运行该任务不产生额外变化（幂等）

#### Scenario: 撤销不删除记录

- **GIVEN** 一条生效中的授权
- **WHEN** 管理员撤销它
- **THEN** 授权记录仍存在且状态为 `revoked`
- **AND** 管理界面仍能列出它（标注为已撤销）

### Requirement: 权限申请与审批流

系统 SHALL 允许用户为自己提交数据集权限申请，申请必须包含理由。

申请单状态 SHALL 遵循状态机：`pending → approved | rejected | cancelled`，
终态不可再流转。

审批通过时，系统 SHALL 自动创建对应的 `DatasetGrant`，并把申请单里的
期望行级规则、字段白名单、有效期应用于该授权。

#### Scenario: 提交申请需要理由

- **GIVEN** 用户对某数据集无权限
- **WHEN** 提交申请但理由为空或过短
- **THEN** 返回 400 并说明理由必填

#### Scenario: 审批通过落成带有效期的授权

- **GIVEN** 一条 `pending` 申请，声明期望规则与 15 天有效期
- **WHEN** 管理员批准
- **THEN** 系统创建一条 `active` 授权给申请人
- **AND** 该授权带 15 天后的 `expires_at`
- **AND** 申请单状态变为 `approved` 并记录落成的授权 id

#### Scenario: 不能审批自己的申请

- **GIVEN** 用户 U 提交的 `pending` 申请
- **WHEN** U 自己调用批准接口
- **THEN** 返回 403（否则申请流失去意义）

#### Scenario: 拒绝必须说明理由

- **GIVEN** 一条 `pending` 申请
- **WHEN** 管理员拒绝但未填理由
- **THEN** 返回 400

#### Scenario: 申请人可撤销自己的申请

- **GIVEN** 用户 U 提交的 `pending` 申请
- **WHEN** U 调用撤销接口
- **THEN** 状态变为 `cancelled`，且后续无法再被批准（409）

#### Scenario: 同一数据集不允许重复待审申请

- **GIVEN** 用户 U 对数据集 D 已有一条 `pending` 申请
- **WHEN** U 再次对 D 提交申请
- **THEN** 返回 409

#### Scenario: 已有权限者不能再申请

- **GIVEN** 用户 U 对数据集 D 已有生效授权
- **WHEN** U 对 D 提交申请
- **THEN** 返回 409

### Requirement: 提交的目标必须已存在

> **Rationale**：用户操作目标必须真实存在；工单目标写错（拼错路径、数错序号、同名不同物）是常见运维事故。
> 系统在收到操作请求时就校验目标，而不是等到审批通过或执行时才由底层抛出难懂的错。
> 该要求不改变任何数据语义，只是把"目标不存在"从隐性错误提前为显性拒绝。

#### Scenario: 提交时校验目标

- **GIVEN** 用户提交一份申请，其目标不存在
- **WHEN** 系统接收该申请
- **THEN** 立即返回明确错误，且不创建任何记录

### Requirement: 审批人可调整最终权限

管理员审批 SHALL 可以收紧或放宽申请人声明的权限。

调整后的行级规则与字段白名单 SHALL 按与管理员直建授权**相同的规则**校验
（字段必须存在于数据集），不因走申请流而放松。

#### Scenario: 调整后的非法规则被拒绝

- **GIVEN** 管理员批准时指定了数据集中不存在的字段
- **WHEN** 调用批准接口
- **THEN** 返回 400，且不创建授权
