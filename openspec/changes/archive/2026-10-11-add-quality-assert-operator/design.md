# Design

## Context

**现状**：算子库 20 个（15 SQL + 5 AI），`Operator` 基类两种引擎：
`engine="sql"`（产 SQL、进 dbt）与 `engine="python"`（AI，先物化再 JOIN）。
质量校验是**规则判定**，不是模型判断 —— 必须走 SQL 引擎，可下推、可物化、零调用成本。

**复用的现有模块**：

- `app/flow/operators/base.py::Operator` + `@register` —— 算子注册与 JSON Schema 目录
- `app/flow/sqlutil.py::sql_literal / q / q_alias` —— 字面量与标识符的安全引用
- `app/flow/context.py::CompileCtx` —— 上游列信息沿 DAG 传播（校验列存在靠它）
- `app/flow/operators/clean.py` 的 filter —— 同类 SQL 生成风格参照

**本项目的既有设计哲学**（`dedup.semantic` 已确立）：**标记不删行** ——
删行不可逆，标记可解释；拦截交给下游 `clean.filter`。

## Goals / Non-Goals

**Goals:**

- 三类规则（完整性/唯一性/有效性）在编译期生成一段纯 SQL 的 CASE 表达式
- 违规原因**人能读懂**（"amount 不得为空；amount 应 >= 0"），下游直接进报表/告警
- 全部规则在编译期校验（列存在、op 与值类型匹配、输出列不冲突）

**Non-Goals:**

- 违规即中止、汇总报告、六维度全量、自定义 SQL 谓词（见 proposal 的 Out of Scope）

## Decisions

### 决策 1：qa_passed 由 qa_reason 推导，而不是两套条件

```sql
"qa_reason" = COALESCE(NULLIF(TRIM(BOTH '；' FROM CONCAT_WS('；', <各规则的违规描述>)), ''), '')
"qa_passed" = ("qa_reason" = '')
```

**理由**：如果 passed 与 reason 各写一套 WHEN 条件，两个表达式可能不一致
（改了规则忘了同步另一半）—— 同一份信息写两遍迟早漂移。
「有违规描述 = 未通过」是恒真推导，**结构上不可能矛盾**。
PostgreSQL 的 `CONCAT_WS` 天然跳过 NULL，恰好实现"通过的规则不产描述"。

### 决策 2：值域规则对 NULL 宽容，空值归 `not_null` 管

`CASE WHEN NOT (amount >= 0) THEN '…' END` 在 amount 为 NULL 时条件是 NULL、不触发 ——
这是 PostgreSQL 三值逻辑的自然结果，**刻意保留**：NULL 是"未知"不是"违规"，
把"未知"报成违规会让报表充满噪音。空值检查明确归 `not_null` 参数，职责分离，
spec 里写成了显式场景。

### 决策 3：唯一性用窗口函数在 SELECT 里表达

组合唯一是**行间**约束，单行 CASE 写不出来。用
`COUNT(*) OVER (PARTITION BY k1, k2) > 1` —— 仍是 SELECT 子句里的表达式，
不需要改编译器（编译器只拼 CTE，表达式随便复杂）。
代价是该窗口对全分区计数（含被上游过滤掉的行？不 —— 上游 CTE 已经是过滤后的结果集，
窗口在结果集上算，语义正确）。

### 决策 4：`in` 的值必须是非空列表，`model_validator` 在参数解析阶段就拦

操作符与值类型的匹配（`in` → 列表、其余 → 标量）在 Pydantic 参数校验层完成，
错误信息落到"参数不合法"这一类 —— 与既有算子的校验路径一致，
LLM 生成流程时的自检（`validate_flow`）也能拦住。

### 决策 5：输出列名可配置，且编译期查重

`passed_column` / `reason_column` 默认 `qa_passed` / `qa_reason`。
可配置是为了"一条流程放多个质量节点"（比如源头标一次、加工后再标一次）。
编译期用 `ctx.columns` 查重：与上游已有列冲突直接报错 ——
**宁可报错也不静默覆盖**，被覆盖的列不会报错只会给错的结果。

## Risks / Trade-offs

- **违规描述里的列名/规则文本进入 SQL 字面量**。描述是算子**自动生成**的
  （列名来自已过 `check_ident` 的上游列、描述模板固定），没有用户自由文本入口；
  唯一性描述里的列名同样来自上游列 —— 注入面关闭。
- **窗口函数在大表上的开销**。PARTITION BY 组合计数在亿级表上不便宜，
  但质量校验本来就该在过滤/收敛后的中间结果上做（画布连线顺序），不是全表扫；
  编译产物进 dbt 后用户可用 EXPLAIN 预估（既有能力）。
- **违规行数多时 reason 列膨胀**。一行最多违观数 = 规则数，文本量可控；
  真出现"规则几十条、全表违规"的极端场景，那是规则配置有问题，先不加防护。
