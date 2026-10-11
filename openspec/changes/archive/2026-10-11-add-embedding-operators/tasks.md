# Tasks: 向量算子

## 1. 向量化基础

- [x] 1.1 实现 `backend/app/ai/embedding.py`：提供方抽象（`auto` / `api` / `local` / `none`）
  + 环境探测 + 不可用时的错误信息（必须同时给出「装本地依赖」与「配 API」两条出路）
  验收：`tests/unit/test_ai_embedding.py` 断言四条路径的选择结果，
  以及「显式指定 local 但缺依赖时报错、不退回 API」——
  其中"本机什么都没配"那条直接跑的就是**真实的拒绝分支**（本机正是这个状态），
  实测 `check_embedding.py` 输出 `当前会使用: none` + 两条出路，exit=1。

- [x] 1.2 实现 `backend/app/ai/similarity.py`：余弦相似度（分块计算，不摊开 N×N 矩阵）
  + 并查集聚类 + 代表行选择（shortest / longest / first）
  验收：单测断言「相似度 ≥ 阈值归同组」「划分与输入顺序无关」「每组恰有一个代表」。
  "顺序无关"那条是选并查集而非贪心质心的直接验证（比较的是划分结构，不是组号）。

- [x] 1.3 `settings.py` 增加 embedding 配置项（provider / model / api_base / api_key /
  batch_size / max_chars）；`pyproject.toml` 加 `numpy`
  验收：本机实测装上 numpy 2.5.4，`get_settings` 能读到全部新项

- [x] 1.4 新增 `backend/scripts/check_embedding.py`：一条命令打印当前选中的提供方与理由
  验收：运行**不下载模型、不发起计费调用**；本机（无依赖无 API）给出可操作的
  两条出路。附带踩坑：`args.try` 是语法错误（`try` 是关键字），改用 `dest="try_run"`。

## 2. 算子

- [x] 2.1 `embed.text`：文本列 → 向量列（JSON 文本，保留 6 位小数）
  验收：单测断言输出列名合法（非 ASCII 列名被拒）、每行一个定长向量、
  向量可解析回等长浮点列表、同一文本两次结果一致

- [x] 2.2 `dedup.semantic`：文本列 → `semantic_group` + `is_representative`
  验收：单测断言输出行数与输入一致（不静默删行）、同组共享组号、
  每组恰有一个代表、**阈值是硬约束**（0.9999 时不合并）、行序保持不变

## 3. 验证与收尾

- [x] 3.1 单测合计覆盖：提供方选择 / 拒绝路径 / 相似度 / 聚类 / 两个算子
  验收：新增 42 例全绿，**不依赖任何模型下载或网络**
  （用确定性假向量走真实代码路径：截断 → 分批 → 维度校验 → 序列化 → 缓存 → 分组）

- [x] 3.2 端到端冒烟 `backend/scripts/embed_smoke.py`：
  本地假 embeddings 服务 → `BISQL_EMBEDDING_API_BASE` 指向它
  → 真实编排流程 `source → dedup.semantic → sink` → 真 dbt 物化
  验收：实测输出
  ```
  id=1  group=0  代表=True   新农哥碧根果
  id=2  group=0  代表=False  新农哥碧根果（袋装）   ← 同组
  id=3  group=1  代表=True   东北松子               ← 不同组
  exit=0
  ```
  验证的是 API 提供方的**真实代码路径**（HTTP 调用、分批、解析、缓存），
  模型本身是假的；真实语义效果需用户配好模型或 API 后自行验证。

- [x] 3.3 全量 `cd backend && python -m pytest -q` + `alembic check`
  验收：**412 passed**（改动前 366，+46）；`No new upgrade operations detected.`
  （本 change 不新增表）

- [x] 3.4 `README.md` 补「向量能力怎么准备」（装本地依赖 / 配 API 两条路）
  验收：README 里能找到这两条路与自检命令，并写明"绝不静默降级"的约定

- [x] 3.5 `openspec validate --all` 全过后归档
  验收：`summary.failed` 为 0

## 实现记录：冒烟撞出的两个既有 bug（AI 算子落地以来就存在）

这两个 bug 说明一件事：**此前的 AI 算子只有 `run_python` 级别的单测，
从来没有一条含 AI 节点的流程真正执行过**。

1. **`run_ai_nodes` 用错了编译函数** —— 它调 `compile_select_for(dsl, node.id)`，
   编到 AI 节点**自己**；而 AI 节点不产 SQL，第一步就抛「需要先由 Python 执行并物化」。
   后果：**任何含 AI 算子的流程从来没跑通过**。
   修复：新增 `compile_input_for`（编到该节点的**输入**为止），`run_ai_nodes` 改用它；
   `compile_select_for` 保留给单步预览（语义是"预览这一步的结果"，含该节点）。
   两个语义分开后，谁该用哪个一目了然，文档里互相指了路。

2. **含 AI 算子的流程保存必然失败** —— 「保存即编译」遇到 AI 节点直接报错
   （AI 产物要等运行时才存在）。
   修复：新增 `FlowNeedsExecutionError`（`FlowCompileError` 的子类，只有
   `context.external_source` 在节点未物化时抛这一种）；
   保存阶段专门接住它并降级为「参数 + 权限已校验」（`_compile_for_save` 返回 None），
   SQL 与血缘留空等运行后补；**其它编译错误照旧抛出** ——
   降级只针对这一种情况，权限检查发生在遍历节点之前，不会被吞。
   验收：`test_saving_a_flow_with_ai_operators_succeeds_without_sql`。
