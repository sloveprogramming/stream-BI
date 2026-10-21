# assistant-agent

## MODIFIED Requirements

### Requirement: Agent 运行记录

系统 SHALL 为每次 Agent 运行落库：运行标识、目标、状态、回答、
步骤数、工具调用次数、耗时、错误信息。

运行记录 SHALL 额外包含本次回答的**置信度档位**与**原因列表**，
供事后复盘"这个结论当时有多可信"。

落库失败 SHALL NOT 影响对外回答。

#### Scenario: 运行记录含置信度

- **GIVEN** 一次完成的 Agent 运行
- **WHEN** 查询运行记录
- **THEN** 记录含置信度档位与原因列表

#### Scenario: 落库失败不影响回答

- **GIVEN** 数据库写入失败
- **WHEN** Agent 完成并给出回答
- **THEN** 回答仍然正常返回给用户

### Requirement: 单步取数与证据采集

Agent 的每一步取数 SHALL 走统一的裁剪入口（安全闸门 → 权限重写 →
只读执行），SHALL NOT 存在其他取数路径。

每个子问题的执行结果 SHALL 记录：执行状态、SQL、返回列、返回行数、
**SQL 改写次数**，以及该步是否命中指标字典与人工样例。

这些字段是置信度判定的输入，也用于事后复盘单步质量。

#### Scenario: 记录改写次数

- **GIVEN** 一个子问题的 SQL 第一次未通过体检、改写后才成功
- **WHEN** 该步完成
- **THEN** 该步记录里的改写次数大于 1

#### Scenario: 记录口径与样例命中

- **GIVEN** 一个子问题的问题文本命中了指标字典
- **WHEN** 该步完成
- **THEN** 该步记录标记命中了指标字典

#### Scenario: 取数仍走唯一入口

- **GIVEN** Agent 需要执行模型生成的 SQL
- **WHEN** 执行该 SQL
- **THEN** 它经过安全闸门校验、权限重写与只读事务包装
