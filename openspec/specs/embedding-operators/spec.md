# embedding-operators Specification

## Purpose
让编排具备**语义层**的处理能力：把「这两条记录说的是不是同一件事」从字符比对
升级为语义比对；并让「业务文本 → 可计算的向量」成为可复用的原子能力。

## Requirements

### Requirement: 文本向量化

系统 SHALL 提供把一列文本转成向量的算子，且向量与源文本的对应关系 MUST 可被下游读回。

#### Scenario: 向量化结果可读回

- **GIVEN** 一列文本经过向量化算子
- **WHEN** 读取产物中的向量列
- **THEN** 每一行 MUST 有一组与该行文本对应的定长浮点向量
- **AND** 该列 MUST 能被解析回等长的浮点列表（不丢维度、不丢精度到无法比较）

#### Scenario: 同一文本复用缓存

- **GIVEN** 同一段文本已经向量化过
- **WHEN** 再次运行且输入与模型未变
- **THEN** 系统 MUST 复用缓存结果
- **AND** MUST NOT 重新计算

#### Scenario: 换模型后缓存失效

- **GIVEN** 已有某模型下的向量缓存
- **WHEN** 更换向量化模型后再次运行
- **THEN** 系统 MUST 重新计算
- **AND** MUST NOT 混用不同模型的向量

### Requirement: 向量化提供方不可用时明确拒绝

系统 SHALL 在向量化提供方（本地模型或 HTTP API）不可用时**中止并说明两条出路**，
MUST NOT 静默降级为其它算法。

#### Scenario: 未配置任何提供方

- **GIVEN** 本机既没有可用的本地模型依赖，也没有配置 HTTP API
- **WHEN** 流程执行到向量算子
- **THEN** 系统 MUST 中止该次运行
- **AND** 错误信息 MUST 同时给出「装本地依赖」与「配 API」两种解决方向
- **AND** MUST NOT 改用字符相似度等替代算法继续产出结果

#### Scenario: 显式指定提供方但它不可用

- **GIVEN** 配置指定使用本地模型，而本机缺少对应依赖
- **WHEN** 流程执行到向量算子
- **THEN** 系统 MUST 报错指出缺少什么
- **AND** MUST NOT 悄悄改用另一条路径

#### Scenario: 探测不产生副作用

- **WHEN** 查询当前环境的向量化可用性
- **THEN** 该查询 MUST NOT 下载模型、MUST NOT 发起计费调用
- **AND** MUST 说明当前选中的提供方与理由

### Requirement: 语义去重

系统 SHALL 提供按语义相似度给文本分组的算子：相似度达到阈值的文本归入同一组，
每组 MUST 有且仅有一个代表行。

#### Scenario: 同一商品的不同写法归入同组

- **GIVEN** 一列商品名称，其中两条指向同一商品但写法不同（如带包装规格后缀）
- **WHEN** 使用语义去重且阈值取默认值
- **THEN** 二者 MUST 被分到同一组
- **AND** 该组 MUST 只有一个代表行

#### Scenario: 语义无关的文本不被合并

- **GIVEN** 语义无关的两段文本
- **WHEN** 使用语义去重
- **THEN** 二者 MUST 分属不同组
- **AND** MUST NOT 因为字面相近就被合并

#### Scenario: 输出行数与输入一致

- **GIVEN** 输入 N 行
- **WHEN** 执行语义去重
- **THEN** 输出 MUST 仍是 N 行
- **AND** 分组信息 MUST 以附加列的形式给出（是否丢弃非代表行由后续算子决定）
- **AND** 系统 MUST NOT 在用户没要求时静默删除行

#### Scenario: 分组结果可解释

- **GIVEN** 若干文本被合并为一组
- **WHEN** 查看产物
- **THEN** 同组各行 MUST 共享同一个组号
- **AND** 代表行的标记 MUST 能直接用于筛选

### Requirement: 不依赖数据库扩展

系统 SHALL 在**未安装向量扩展的 PostgreSQL** 上完成文本向量化与语义去重。

#### Scenario: 普通 PostgreSQL 上可用

- **GIVEN** 数据库是未安装 pgvector 的 PostgreSQL
- **WHEN** 执行文本向量化与语义去重
- **THEN** 系统 MUST 正常完成
- **AND** MUST NOT 要求用户先安装数据库扩展
