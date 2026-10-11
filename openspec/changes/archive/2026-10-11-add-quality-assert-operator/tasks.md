# Tasks: 质量校验算子

## 1. 算子

- [x] 1.1 `backend/app/flow/operators/quality.py`：`quality.assert`（engine=sql）
  参数 `not_null` / `unique`（组合键）/ `rules`（结构化 op+value）/ `passed_column` / `reason_column`；
  输出 `qa_passed` + `qa_reason` 两附加列；注册进算子目录（算子库 21 个）
  验收：20 例单测 —— SQL 形状（CASE/CONCAT_WS/窗口函数/IS FALSE/引号转义）、
  行数不变、输出列沿 DAG 传播、自定义输出列名生效

- [x] 1.2 参数校验：三类规则全空拒绝；`in` 必须非空列表、其余必须标量；
  输出列名过 `check_ident` 且与上游列查重；引用不存在的列在编译期报错
  验收：spec 的全部错误路径各有用例（含 `between` 这类未知操作符被拒）

## 2. 验证与收尾

- [x] 2.1 `scripts/flow_smoke.py` 加第三条「质量链」流程：
  源 → 去重 → 过滤 → quality.assert（not_null + 两条值域规则）→ 物化
  验收：真 dbt 跑通，实测
  ```
  materialized: [(2, 20, True, ''), (4, 30, False, 'amount 应 ≤ 25（不满足）')]
  ```
  行数与上游一致；必过行原因为空；必违行被标记且原因可读

- [x] 2.2 全量 `python -m pytest -q` + `alembic check`
  验收：**458 passed**（改动前 438，+20）；`No new upgrade operations detected.`
  （不新增表，符合预期）

- [x] 2.3 `README.md` 冒烟一节改为三条流程四件事，质量链的"不删行"语义写明
  验收：README 里能找到

- [x] 2.4 `openspec validate --all` 全过后归档，提交
  验收：`summary.failed` 为 0

## 实现记录

- **qa_passed 由 qa_reason 推导**（`reason = ''` 即通过）：passed 与 reason 出自同一份条件，
  结构上不可能出现"标记通过却带原因"的矛盾 —— 比两套 WHEN 条件少一类漂移。
- **值域规则对 NULL 宽容**（`(expr) IS FALSE` 让三值逻辑自然工作）：NULL 是"未知"不是
  "违规"，空值归 `not_null` 管 —— 这是 spec 的显式场景，真 PG 行为测试锁住。
- **违规描述里的单引号必须转义**（`_safe_text`）：描述进 SQL 字面量，
  用户值里的 `'` 不处理就是注入口；列名来自已过 `check_ident` 的上游列，安全。
- 前端零改动：算子目录走 JSON Schema 自动渲染表单 —— "加算子不改前端"的第一次直接兑现。
