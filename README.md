# BIsql 数据分发中台

设计文档：docs/superpowers/specs/2026-09-08-data-distribution-platform-design.md
当前进度：P1（同步引擎）

## 本地开发

1. 启动依赖（二选一）：本机原生 PostgreSQL 16（`localhost:55432`，默认连接串已指向它）；或 `docker compose up -d`（PG 55432 / ERP MySQL 53306）
2. `cd backend && python -m pip install -e ".[dev]"`
3. `copy .env.example .env`（按需改连接串）
4. `alembic upgrade head`
5. `python -m uvicorn app.main:app --reload`
6. 健康检查：GET http://127.0.0.1:8000/api/v1/health

## 测试

cd backend && python -m pytest -v
