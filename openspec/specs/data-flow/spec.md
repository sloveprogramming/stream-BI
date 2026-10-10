# 数据编排 Specification

## Purpose

让使用者在画布上把 ERP 原始数据加工成业务口径的表，并让**产物继续受同一套权限约束**。

编排是本系统与普通 ETL 工具的分界点：它既有可视化算子库（含 LLM 算子），
又不会成为绕过权限的后门——权限裁剪发生在编译期，而不是运行后过滤。

## Requirements

### Requirement: 流程定义与版本

系统 SHALL 把画布上的编排保存为版本化的 DSL；
每次保存产生新版本，历史版本不可变且可回滚。

#### Scenario: 保存产生新版本

- **GIVEN** 某流程当前版本号为 1
- **WHEN** 用户修改后保存
- **THEN** 系统 MUST 产生版本 2
- **AND** 版本 1 的 DSL 与编译产物 MUST 保持原样

#### Scenario: 回滚不删历史

- **GIVEN** 某流程已有版本 1、2、3
- **WHEN** 用户回滚到版本 1
- **THEN** 系统 MUST 以版本 1 的内容创建一个新版本
- **AND** MUST NOT 删除任何历史版本

#### Scenario: 保存时即校验权限

- **GIVEN** 流程 owner 对源数据集没有任何授权且不是管理员
- **WHEN** 用户保存该流程
- **THEN** 系统 MUST 拒绝保存并说明缺少哪个数据集的授权
- **AND** MUST NOT 留下半成品流程

### Requirement: 流程合法性校验

系统 SHALL 在保存与运行前校验流程结构：恰好一个输入节点与一个输出节点、
无环、无孤立节点、节点标识规范化后不重复、目标表名是合法标识符。

#### Scenario: 拒绝成环

- **WHEN** 用户把流程连成一个环
- **THEN** 系统 MUST 拒绝保存并说明原因

#### Scenario: 拒绝多个输出节点

- **WHEN** 流程中存在两个输出节点
- **THEN** 系统 MUST 拒绝保存并说明原因

#### Scenario: 拒绝非法产物表名

- **WHEN** 目标表名不是合法标识符（含空格、以数字开头、含中文）
- **THEN** 系统 MUST 拒绝保存并说明命名要求

### Requirement: 权限裁剪发生在编译期

系统 SHALL 在编译时把每个数据源节点替换为**带行级权限条件的子查询**，
使算子层无法获得裸表名，从而在结构上不可能绕过权限。

#### Scenario: 源表永远带行级条件

- **GIVEN** 流程 owner 对源数据集有 `warehouse = '德清'` 的行级规则
- **WHEN** 系统编译该流程
- **THEN** 生成的 SQL MUST 包含该行级条件
- **AND** 源表 MUST NOT 以不带该条件的形式出现在 SQL 中

#### Scenario: 无授权导致运行中止

- **GIVEN** 流程 owner 对源数据集没有任何授权且不是管理员
- **WHEN** 运行该流程
- **THEN** 运行状态 MUST 为 aborted
- **AND** 系统 MUST NOT 进入执行阶段
- **AND** MUST NOT 产出任何表或数据集

#### Scenario: Python（AI）算子拿不到数据库连接

- **WHEN** 系统执行一个 AI 算子节点
- **THEN** 该算子 MUST 只能消费「已按权限裁剪过的」上游数据
- **AND** MUST NOT 具备自行取数的能力

### Requirement: 产物继承行级权限

系统 SHALL 让产物数据集继承来源行的行级规则；
若某条规则的字段在产物中不存在，则 MUST 禁止分享该数据集（强制仅 owner 可见）。

#### Scenario: 规则被继承

- **GIVEN** owner 的行级规则为 `warehouse = '德清'`
- **WHEN** 流程产物注册为数据集
- **THEN** 产物的授权 MUST 含同一条行级规则

#### Scenario: 规则无法继承时禁止分享

- **GIVEN** 流程把 `warehouse` 列从产物中丢弃
- **AND** DSL 中配置了把产物分享给其他用户
- **WHEN** 流程成功运行
- **THEN** 产物的授权 MUST 仅包含 owner
- **AND** 运行记录 MUST 包含「行级规则无法继承」的告警
- **AND** 该告警 MUST NOT 被当作运行失败

### Requirement: 产物自动成为数据集

系统 SHALL 在流程成功后把产物注册为数据集，
使其自动获得权限裁剪与全部出口（REST / MCP / 问数 / 导出）的能力。

#### Scenario: 产物可被各出口消费

- **GIVEN** 某流程已成功运行并注册了数据集
- **WHEN** 有权限的用户通过任一出口读取该产物
- **THEN** 系统 MUST 正常返回
- **AND** 行级规则 MUST 生效

#### Scenario: 重复运行不产生重复数据集

- **WHEN** 同一条流程运行两次
- **THEN** 系统 MUST 只保留一个产物数据集
- **AND** 授权 MUST 被重建而非追加

### Requirement: 执行结果可追溯

系统 SHALL 为每次运行记录状态、产出行数、耗时、错误信息，
以及**本次实际套用的行级规则快照**。

#### Scenario: 记录行权快照

- **WHEN** 一条流程运行完成
- **THEN** 运行记录 MUST 包含本次生效的行级规则快照
- **AND** 快照 MUST 与生成 SQL 时所使用的那一份一致

#### Scenario: 权限拒绝与执行失败被区分

- **WHEN** 运行因权限不足被拒绝
- **THEN** 状态 MUST 记为 aborted
- **WHEN** 运行因引擎报错失败
- **THEN** 状态 MUST 记为 failed

### Requirement: 单步预览

系统 SHALL 支持只运行「到某个节点为止」的链路并返回前若干行，
供用户在编排过程中确认每一步的结果。

#### Scenario: 预览同样受权限约束

- **GIVEN** owner 的行级规则为 `warehouse = '德清'`，且源表另有安徽的行
- **WHEN** 用户在画布上预览任一节点
- **THEN** 预览结果 MUST 只包含德清的行
- **AND** 预览所执行的 SQL MUST 包含该行级条件

### Requirement: LLM 算子的成本可控

系统 SHALL 对 AI 算子施加三重成本控制：同输入去重、跨运行结果缓存、分批并发；
并对单次输入规模设置上限，超限时中止而非截断。

#### Scenario: 同输入不重复调用模型

- **GIVEN** 某列中有多行文本完全相同
- **WHEN** 该列经过 AI 分类算子
- **THEN** 相同的文本 MUST 只被送往模型一次
- **AND** 输出行数 MUST 与输入行数一致

#### Scenario: 跨运行复用缓存

- **GIVEN** 同一算子与同一参数已运行过一次
- **WHEN** 再次运行且输入未变
- **THEN** 系统 MUST 复用缓存结果
- **AND** MUST NOT 再次调用模型

#### Scenario: 参数变化时缓存失效

- **GIVEN** 已存在某标签集合下的缓存
- **WHEN** 用户修改标签集合后再次运行
- **THEN** 系统 MUST 重新调用模型
- **AND** MUST NOT 复用旧标签

#### Scenario: 超限中止而非截断

- **WHEN** AI 算子的输入行数达到配置上限
- **THEN** 系统 MUST 中止该次运行并说明原因
- **AND** MUST NOT 产出「只标注了一部分」的结果表
