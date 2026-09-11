FROM python:3.12-slim
WORKDIR /srv/bisql
ENV PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" \
    "psycopg[binary]>=3.2" "pymysql>=1.1" "cryptography>=42" \
    "apscheduler>=3.10,<4" "pydantic-settings>=2.3" "httpx>=0.27" \
    "pyjwt>=2.8" "openpyxl>=3.1" "alembic>=1.13" "mcp>=1.10,<2" \
    "openai>=1.0" "sqlglot>=25.0"
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY frontend/dist ./frontend/dist
EXPOSE 8000
# 启动前先跑迁移：避免「模型已更新但库结构没跟上」。迁移失败则容器不启动（快速失败）
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
