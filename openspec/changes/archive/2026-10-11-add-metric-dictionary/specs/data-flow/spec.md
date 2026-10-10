# Spec Delta

## ADDED Requirements

### Requirement: 派生字段可引用指标口径

系统 SHALL 允许 `field.derive` 算子以指标名称（而非裸表达式）作为计算口径，
并在编译期展开为该指标定义的计算公式。

#### Scenario: 引用指标而非手写公式

- **GIVEN** 指标字典中已定义某指标
- **WHEN** 流程的派生字段节点引用该指标
- **THEN** 编译出的 SQL MUST 使用该指标定义的计算公式
- **AND** DSL 中 MUST 记录的是指标名，而不是展开后的表达式

#### Scenario: 表达式与指标名互斥

- **WHEN** 一个派生字段节点同时提供了表达式与指标名
- **THEN** 系统 MUST 拒绝该配置并说明只能二选一

#### Scenario: 未知指标给出可用清单

- **WHEN** 派生字段引用了不存在的指标
- **THEN** 错误信息 MUST 列出可用的指标名
- **AND** 该错误 MUST 在保存或编译阶段抛出，而不是运行阶段

#### Scenario: 未填口径来源被拦下

- **WHEN** 一个派生字段节点既没有表达式也没有指标名
- **THEN** 系统 MUST 在各处一致地拒绝该配置
