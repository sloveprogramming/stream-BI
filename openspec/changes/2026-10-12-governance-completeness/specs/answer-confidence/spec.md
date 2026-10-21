# answer-confidence

## ADDED Requirements

### Requirement: 回答附带可解释的置信度

每次问数回答 SHALL 附带置信度，取值为 `high` / `medium` / `low`，
并附带**非空**的原因列表（机器可读，供界面原样展示）。

置信度 SHALL 由**确定性信号**判定，SHALL NOT 交给模型自评 ——
模型对"我不确定"没有可靠的自我认知，且其不确定感与事实正确性不相关。

判定信号 SHALL 包括：证据行数、失败子问题数、SQL 改写次数、
指标字典命中情况、人工样例命中情况、自检结论。

#### Scenario: 任何一档都给出原因

- **GIVEN** 任意一组判定信号
- **WHEN** 计算置信度
- **THEN** 结果的原因列表非空

#### Scenario: 三档有序

- **GIVEN** 三档置信度
- **WHEN** 比较它们的分值
- **THEN** `high` 高于 `medium`，`medium` 高于 `low`

### Requirement: 判定规则

系统 SHALL 按以下规则定档（从高往低，命中即定档）：

- `low`：证据行数为 0，或有子问题查询失败，或自检判定证据不足；
- `medium`：发生过 SQL 改写重试，或未命中指标字典，或未命中人工样例；
- `high`：其余情况（SQL 一次通过且有口径依据）。

自检结论 SHALL 只作为**降档**信号：模型说"证据足够"不作为升档依据，
但模型说"证据不足"是有价值的警告。

#### Scenario: 没有证据即低可信

- **GIVEN** 所有子问题都没有返回任何行
- **WHEN** 计算置信度
- **THEN** 结果为 `low`，原因是"没有任何查询结果作为证据"

#### Scenario: 有子问题失败即低可信

- **GIVEN** 3 个子问题中 2 个查询失败
- **WHEN** 计算置信度
- **THEN** 结果为 `low`，原因里给出失败子问题数量

#### Scenario: 自检不足即降档

- **GIVEN** 证据充足但自检判定 `enough=false`
- **WHEN** 计算置信度
- **THEN** 结果为 `low`

#### Scenario: 自检充足不能升档

- **GIVEN** 证据行数为 0 但自检判定 `enough=true`
- **WHEN** 计算置信度
- **THEN** 结果仍为 `low`

#### Scenario: SQL 改写过后为中等可信

- **GIVEN** SQL 改写了 3 次才通过校验
- **WHEN** 计算置信度
- **THEN** 结果为 `medium`，原因里给出改写次数

#### Scenario: 口径未命中字典为中等可信

- **GIVEN** 问题中的业务词没有命中指标字典
- **WHEN** 计算置信度
- **THEN** 结果为 `medium`，原因是口径按字面理解

#### Scenario: 一次通过且有依据为高可信

- **GIVEN** SQL 一次通过、口径命中字典、命中人工样例、有返回行
- **WHEN** 计算置信度
- **THEN** 结果为 `high`

### Requirement: 两条问数链路口径一致

单轮问数（`/assistant/chat`）与 Agent 问数（`/assistant/ask`）
SHALL 使用同一套判定逻辑，避免同一问题在两条链路上得到不同的置信度口径。

单轮问数没有重试循环，其 SQL 改写次数 SHALL 视为 1。

#### Scenario: Agent 回答带置信度

- **GIVEN** 一次成功的 Agent 运行
- **WHEN** 推送回答事件
- **THEN** 事件里包含 `confidence.level` 与 `confidence.reasons`

#### Scenario: 单轮问数带置信度

- **GIVEN** 一次成功的单轮问数
- **WHEN** 推送事件流
- **THEN** 其中有 `confidence` 事件，含 level 与 reasons

#### Scenario: 查询失败即低可信

- **GIVEN** 单轮问数在安全闸门或执行阶段失败
- **WHEN** 给出置信度
- **THEN** 结果为 `low`，原因里包含失败说明

### Requirement: 置信度落库

Agent 运行的置信度与原因 SHALL 落库到运行记录，供事后复盘。

落库失败 SHALL NOT 影响对外回答。

#### Scenario: 运行记录带置信度

- **GIVEN** 一次持久化的 Agent 运行
- **WHEN** 查询该运行记录
- **THEN** 记录中包含置信度档位与原因列表
