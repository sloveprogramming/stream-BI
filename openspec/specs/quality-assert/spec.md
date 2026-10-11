# quality-assert Specification

## Purpose
让「数据合不合格」在数据流程内就被回答：违规行带着**人能读懂的原因**流向下游，
脏数据可追溯、可拦截，而不是等报表错了再倒查。

## Requirements

### Requirement: 行级质量标记

系统 SHALL 提供 `quality.assert` 算子：按配置的规则逐行判定，
输出「是否通过」与「违规原因」两个附加列；
**输出行数 MUST 与输入一致**，系统 MUST NOT 删除或中止任何行。

#### Scenario: 空值被标记（完整性）

- **GIVEN** 规则要求 `amount` 不得为空，且输入中存在 `amount` 为空的行
- **WHEN** 经过质量校验算子
- **THEN** 该行的通过标记 MUST 为「未通过」
- **AND** 违规原因 MUST 指明是 `amount` 不得为空

#### Scenario: 组合键重复被标记（唯一性）

- **GIVEN** 规则要求 `order_id, warehouse` 组合唯一，输入中存在两行组合相同
- **WHEN** 经过质量校验算子
- **THEN** 这两行的通过标记 MUST 均为「未通过」
- **AND** 违规原因 MUST 指明该组合重复

#### Scenario: 违反值域规则被标记（有效性）

- **GIVEN** 规则要求 `amount >= 0`，输入中存在 `amount = -5` 的行
- **WHEN** 经过质量校验算子
- **THEN** 该行 MUST 被标记为未通过
- **AND** 原因 MUST 包含该规则的可读描述

#### Scenario: 全部通过时

- **GIVEN** 输入的每一行都满足全部规则
- **WHEN** 经过质量校验算子
- **THEN** 每行的通过标记 MUST 为「通过」
- **AND** 违规原因 MUST 为空

#### Scenario: 一行违反多条规则时原因合并

- **GIVEN** 某行同时违反「不得为空」与「值域」两条规则
- **WHEN** 经过质量校验算子
- **THEN** 违规原因 MUST 同时包含两条描述
- **AND** 通过标记 MUST 只有一个「未通过」

### Requirement: 规则配置的结构化与校验

规则 MUST 以结构化参数表达（列名、操作符、值），MUST NOT 接受裸 SQL 片段；
配置不合法时系统 MUST 在编译前报错并指出问题。

#### Scenario: 未配置任何规则

- **WHEN** 质量校验算子的三类规则全为空
- **THEN** 系统 MUST 拒绝该节点并说明至少需要一条规则

#### Scenario: 引用不存在的列

- **GIVEN** 某条规则引用了上游输出中不存在的列
- **WHEN** 编译流程
- **THEN** 系统 MUST 报错并指明算子、缺失的列与现有列

#### Scenario: 操作符与值类型不匹配

- **GIVEN** 使用 `in` 操作符但值不是列表
- **WHEN** 保存或编译该流程
- **THEN** 系统 MUST 拒绝并说明 `in` 需要列表值

#### Scenario: 输出列名与上游冲突

- **GIVEN** 上游已有名为 `qa_passed` 的列
- **WHEN** 编译流程
- **THEN** 系统 MUST 拒绝并提示换一个输出列名

### Requirement: 空值不误判为值域违规

值域规则（`eq/ne/gt/ge/lt/le/like/in`）MUST NOT 把 NULL 判为违规 ——
空值是否违规由完整性规则（`not_null`）负责，两类规则职责分离。

#### Scenario: NULL 不触发值域违规

- **GIVEN** 规则要求 `amount >= 0`，某行 `amount` 为空，且未配置 `not_null`
- **WHEN** 经过质量校验算子
- **THEN** 该行 MUST 被判为「通过」
- **AND** 空值是否算问题由用户另行配置 `not_null` 决定
