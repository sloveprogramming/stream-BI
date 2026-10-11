# Tasks: 数据血缘

## 1. 事件与追溯

- [x] 1.1 `backend/app/models/lineage.py`：`LineageEvent` 表
  （event_type / job_name / run_id / flow_id / payload JSONB / created_at）+ 导出 + alembic 迁移
  验收：迁移 `f3a9b4c2d5e8` 可升降级；`alembic check` 无差异

- [x] 1.2 `backend/app/flow/lineage.py`：`build_run_event` 构造 OpenLineage RunEvent；
  `upstream` / `downstream` BFS 双向追溯（depth 上限 6、防环、防重复、物理源叶子标注）
  验收：单测 —— 事件结构合规、inputs 排序稳定、**环不死循环且起点不重复报告**、
  上游追到物理叶子、depth 截断

- [x] 1.3 `runner.py` 成功路径 emit 事件（与运行记录同一事务落库；失败/中止不 emit）
  验收：单测 —— 成功恰产生 1 条 COMPLETE 事件（inputs/outputs/run_id 全对）、
  失败运行 0 条

## 2. 查询接口与前端

- [x] 2.1 `backend/app/api/lineage_api.py`：`GET /lineage/datasets/{name}/upstream|downstream`
  （登录即可查；响应只含名字与结构）+ 挂载 `main.py`
  验收：无授权用户可查结构（design 决策 2 的语义），响应不含列清单与数据

- [x] 2.2 前端 `DatasetQuery.tsx` 加「血缘」折叠区块（懒加载：展开才查）：
  上游/下游分层列表，节点 Tag 带经由流程名、物理源标橙色
  验收：`tsc -b && vite build` 通过（1.62s）

## 3. 验证与收尾

- [x] 3.1 `scripts/flow_smoke.py` 跑完权限链后加血缘断言（事件 COMPLETE、
  inputs 含源、outputs 是产物、双向追溯命中）
  验收：冒烟 exit=0

- [x] 3.2 全量 `python -m pytest -q` + `alembic check`
  验收：**467 passed**（改动前 458，+9）；`No new upgrade operations detected.`

- [x] 3.3 `README.md` 补「数据血缘」一节（事件语义、双向追溯、为何不做权限裁剪）
  验收：README 里能找到

- [x] 3.4 `openspec validate --all` 全过后归档，提交
  验收：`summary.failed` 为 0

## 实现记录

- **`input_datasets` 的真实格式是 `[{"id":…, "name":…}]`**（编译产物顺手带了 id），
  不是纯字符串列表 —— 事件 payload 直接透传会不合规（OpenLineage 的 dataset 是
  `{namespace, name}`）、追溯 key 也会变成 dict。加了 `_input_names()` 在事件与
  追溯两个消费口统一归一，将来格式再变只改一处。
- **防环的语义**：追溯从 X 出发，X 的上游链若绕回 X，起点的重复出现被抑制 ——
  「环即停」的正确形态是"只报告新节点"，而不是把起点再报一遍（测试断言按此修正）。
- **塞行测试要过 datasets 的 NOT NULL**：`source_table` / `pk_columns` / `columns`
  都不可空（空串/空列表占位）—— 血缘测试里踩了两轮才凑齐。
- ⚠️ **环境教训：绝不用 PowerShell 的 `Get-Content | -replace | Set-Content`
  改含中文的代码文件** —— 按 GBK 读 UTF-8 会把整个文件写坏（SyntaxError U+E21E），
  只能整文件重写。改文件一律用编辑工具。
