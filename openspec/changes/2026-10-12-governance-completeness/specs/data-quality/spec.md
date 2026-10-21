# data-quality

## ADDED Requirements

### Requirement: 数据集级质量巡检

系统 SHALL 支持按数据集配置质量巡检规则，由调度器定时执行。

每条规则 SHALL 声明：规则类型、阈值、所属数据集、取数身份（owner）。
规则类型 SHALL 支持三类（刻意只做这三类，覆盖中台被质疑的绝大多数场景）：

- `freshness`：最新数据的更新时间距现在超过阈值（小时）即异常；
- `row_count`：行数相对上一期的涨跌幅超过阈值（百分比）即异常；
- `null_rate`：指定字段的空值率超过阈值（百分比）即异常。

巡检与编排内的 `quality.assert` 算子 SHALL 保持为两件事：
前者是数据集级的运营期值守，后者是单次运行的研发期自检。

#### Scenario: 新鲜度超阈值命中

- **GIVEN** 一条 `freshness` 规则，阈值为 0.1 小时
- **AND** 数据的最新更新时间是 1 小时前
- **WHEN** 执行巡检
- **THEN** 结果为 `anomaly`，且说明里给出实测小时数与阈值

#### Scenario: 新鲜度未超阈值不命中

- **GIVEN** 同上规则但阈值为 24 小时
- **WHEN** 执行巡检
- **THEN** 结果为 `ok`

#### Scenario: 未指定时间字段时自动识别

- **GIVEN** 一条 `freshness` 规则未指定 `timestamp_field`
- **AND** 数据集包含 `modified` 列
- **WHEN** 执行巡检
- **THEN** 系统使用 `modified` 判定，不报错

#### Scenario: 无法识别时间字段时明确报错

- **GIVEN** 一条 `freshness` 规则未指定 `timestamp_field`
- **AND** 数据集中没有可识别的更新时间列
- **WHEN** 执行巡检
- **THEN** 结果为 `failed`，错误信息说明需要显式指定 `timestamp_field`

#### Scenario: 空值率超阈值命中

- **GIVEN** 一条 `null_rate` 规则指向 `amount`，阈值 10%
- **AND** 该字段实际空值率约 33%
- **WHEN** 执行巡检
- **THEN** 结果为 `anomaly`

#### Scenario: 空值率规则必须指定字段

- **GIVEN** 一条 `null_rate` 规则未指定 `target_field`
- **WHEN** 执行巡检
- **THEN** 结果为 `failed` 并说明原因

### Requirement: 行数环比需要基线

`row_count` 规则 SHALL 用上一期巡检结果作为基线计算环比。

首次巡检（无历史结果）SHALL NOT 判定为异常，只记录当前行数 ——
宁可漏报，不要用不足的样本瞎报。

存库的 `value` SHALL 是**行数的绝对值**而非百分比，
否则下一期无法再算环比（存百分比会一期比一期虚）。

#### Scenario: 首次巡检不判定

- **GIVEN** 一条 `row_count` 规则从未运行过
- **WHEN** 执行巡检
- **THEN** 结果为 `ok`，`value` 为当前行数，`baseline` 为空
- **AND** 说明里注明"首次巡检，无基线"

#### Scenario: 有基线时命中跌幅

- **GIVEN** 上一期结果为 10 行
- **AND** 当前实际行数为 3，阈值 20%
- **WHEN** 执行巡检
- **THEN** 结果为 `anomaly`，`baseline=10`，`change_pct` 约为 -70

#### Scenario: 只报上涨方向时忽略下跌

- **GIVEN** 一条 `direction='up'` 的 `row_count` 规则
- **AND** 行数在下跌
- **WHEN** 执行巡检
- **THEN** 结果为 `ok`

### Requirement: 巡检失败不破坏调度

单条规则的配置错误或执行异常 SHALL 被记录为该规则的一次 `failed` 结果，
SHALL NOT 向上抛出异常终止调度线程。

`failed` 结果本身 SHALL 可在看板上看到 —— "这条规则一直失败"是要被发现的信息。

#### Scenario: 字段不存在时记 failed 而非崩溃

- **GIVEN** 一条 `null_rate` 规则指向数据集中不存在的字段
- **WHEN** 执行巡检
- **THEN** 落库一条 `failed` 结果，包含可读的错误说明
- **AND** 调度器继续运行其他规则

### Requirement: 巡检 SQL 不接受拼接输入

表名与字段名 SHALL 全部经过标识符白名单校验（ASCII 标识符），
值 SHALL 全部参数化。规则里的字段名 SHALL 被视为不可信输入。

#### Scenario: 字段名带注入片段时被拒绝

- **GIVEN** 一条 `null_rate` 规则，`target_field` 含引号与分号
- **WHEN** 执行巡检
- **THEN** 结果为 `failed`，且不生成任何 SQL

#### Scenario: 表名不合法时被拒绝

- **GIVEN** 数据集的 `source_table` 不是 `schema.table` 形式或含非法字符
- **WHEN** 执行巡检
- **THEN** 结果为 `failed`

### Requirement: 巡检异常记审计

巡检命中异常时，系统 SHALL 在审计日志中记录一条 `quality.anomaly`，
包含规则 id、规则名、实测值、基线与人类可读的说明。

#### Scenario: 异常留下审计

- **GIVEN** 一条会命中异常的规则
- **WHEN** 执行巡检
- **THEN** 审计日志中出现一条 `quality.anomaly`，其 detail 含该规则 id

### Requirement: 巡检以 owner 身份取数

巡检 SHALL 以规则声明的 `owner_user_id` 的授权取数，
使巡检看到的数据范围与业务人员一致。

规则 owner 对该数据集无授权时，巡检 SHALL 结果为 `failed` 并说明原因。

#### Scenario: owner 无授权时巡检失败

- **GIVEN** 一条规则的 owner 对目标数据集没有授权
- **WHEN** 执行巡检
- **THEN** 结果为 `failed`，错误信息说明 owner 无授权
