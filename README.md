# BIsql 数据中台

为企业 ERP 数据构建的统一权限分发中台：让业务人员与大模型 Agent 在**同一套权限口径**下安全取数。

- 需求与演进：[docs/2026-10-10-BIsql数据中台改造方案.md](docs/2026-10-10-BIsql数据中台改造方案.md)
- 技术选型：[docs/2026-10-10-BIsql数据中台技术方案-v2-主流技术栈.md](docs/2026-10-10-BIsql数据中台技术方案-v2-主流技术栈.md)
- 模块实现设计：[docs/2026-10-10-BIsql模块实现设计.md](docs/2026-10-10-BIsql模块实现设计.md)
- 原始设计文档：docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md

## 核心设计：一套裁剪器，所有出口复用

行级规则 + 字段白名单由**同一个查询构造器**（`app/access/`）负责，
REST API / Excel·CSV 导出 / MCP 工具 / 自然语言问数 / **数据编排产物** 全部经过它。
所以「同一问题、同一权限、同一结果」是架构保证，不是靠人守规范。

数据编排（`app/flow/`）把画布上的流程编译成 dbt model 时，
源节点会被替换成**带行级权限 WHERE 的子查询**，算子层拿不到裸表名 ——
编排在结构上不可能绕过权限。

## 目录

```
backend/
├── app/
│   ├── access/      权限元数据与查询裁剪（行级 + 字段级）
│   ├── api/         REST 接口（datasets / flows / admin / reports …）
│   ├── assistant/   自然语言问数、LangGraph Agent、评测
│   ├── flow/        数据编排：DSL / 算子库 / 编译器 / 执行器
│   ├── monitor/     主动监控与异常归因
│   ├── sync/        ERP → ods 增量同步（水位线 + 结构自动演进）
│   └── models/      SQLAlchemy 模型
├── dbt/             dbt 项目（编排生成的 model 落在 models/generated/）
└── scripts/         运维与冒烟脚本
frontend/            React + TypeScript 管理台
```

## 本地开发

1. 启动依赖（二选一）：本机原生 PostgreSQL 16（`localhost:55432`，默认连接串已指向它）；或 `docker compose up -d`（PG 55432 / ERP MySQL 53306）
2. `cd backend && python -m pip install -e ".[dev]"`
3. `copy .env.example .env`（按需改连接串）
4. `alembic upgrade head`
5. `python -m uvicorn app.main:app --reload`
6. 健康检查：GET http://127.0.0.1:8000/api/v1/health

## 测试

```
cd backend && python -m pytest -v
```

## 数据编排冒烟

一条命令用真 dbt 跑完整链路（建流程 → 编译 → 物化 → 注册数据集 → 验证行级权限）：

```
cd backend && python scripts/flow_smoke.py --cleanup
```

看点有两个：物化结果里不该出现越权仓的行；dbt 日志里的表名应是 `dwd.xxx` 而非 `dwd_dwd.xxx`。

## 约定

- 新增查询路径一律用 `resolve_grant`（带管理员兜底），不要用 `get_effective_grant`，否则管理员会 403。
- 取数只走 `app/assistant/tools.py` 这一个入口，不要在别处手拼 SELECT。
- 物理列名保持 ASCII；中文展示名填数据集的字段注释（`datasets.column_comments`）。
- 新增模型必须配 alembic 迁移，并在 `app/models/__init__.py` 导出。
