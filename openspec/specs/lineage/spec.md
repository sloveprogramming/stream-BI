# lineage Specification

## Purpose
把「这批数怎么来的、改它会砸到谁」从人肉翻表变成一次查询：
编排每次成功运行落一条标准血缘事件，任意数据集可双向追溯。

## Requirements

### Requirement: 编排成功后落血缘事件

系统 SHALL 在编排运行**成功**后落一条 OpenLineage `RunEvent` 事件，
事件 MUST 包含流程标识、运行标识、输入数据集与产物数据集；
**失败或权限中止的运行 MUST NOT 产生血缘事件**（血缘只记"真的发生了的加工"）。

#### Scenario: 成功运行产生事件

- **GIVEN** 一条流程（输入数据集 A）成功运行并物化产物 B
- **WHEN** 查看血缘事件
- **THEN** MUST 存在一条事件，其 job 为该流程、run 为本次运行
- **AND** inputs MUST 含 A，outputs MUST 含 B
- **AND** eventType MUST 为 COMPLETE

#### Scenario: 失败运行不产生事件

- **GIVEN** 同一条流程因 dbt 构建失败而结束
- **WHEN** 查看血缘事件
- **THEN** 本次运行 MUST NOT 有 COMPLETE 事件

#### Scenario: 事件格式可被标准工具消费

- **WHEN** 读取事件的 payload
- **THEN** 结构 MUST 遵循 OpenLineage RunEvent（run / job / inputs / outputs / producer）

### Requirement: 双向血缘追溯

系统 SHALL 支持对任意数据集按**上游**与**下游**两个方向追溯，
返回分层结构；血缘边由「流程输入 → 产物」与「数据集 → 物理源表」构成。

#### Scenario: 从产物向上追溯

- **GIVEN** 产物 B 由流程 F 从数据集 A 加工而来
- **WHEN** 查询 B 的上游
- **THEN** 第一层 MUST 包含 A（经 F 加工）
- **AND** 若 A 本身是另一条流程的产物，MUST 继续向上追溯

#### Scenario: 从源头向下做影响分析

- **GIVEN** 产物 B 由流程 F 从数据集 A 加工而来
- **WHEN** 查询 A 的下游
- **THEN** 第一层 MUST 包含 B（由 F 产出）

#### Scenario: 追溯到物理源

- **GIVEN** 数据集 A 的 `source_table` 指向 `ods.sales_order`
- **WHEN** 查询 A 的上游
- **THEN** 末端 MUST 标注其物理来源表

#### Scenario: 血缘中出现环时不死循环

- **GIVEN** 流程把产物注册为另一条流程的输入，且（误配置地）构成环
- **WHEN** 追溯
- **THEN** 系统 MUST 在达到深度上限或发现已访问节点时停止
- **AND** MUST NOT 重复报告同一节点

### Requirement: 血缘只暴露结构不暴露数据

血缘查询 MUST 只返回数据集名、流程名、层级与物理源表；
MUST NOT 返回数据行、行数或列清单，MUST NOT 要求拥有数据集授权。

#### Scenario: 无授权用户可看结构

- **GIVEN** 用户对数据集 A 无任何授权
- **WHEN** 该用户查询 A 的血缘
- **THEN** 系统 MUST 返回名字与层级结构
- **AND** 响应 MUST NOT 包含 A 的列清单或任何数据内容
