"""租户（组织）模型：多租户隔离的根。

## 为什么不用 Postgres RLS

市场主流产品（Metabase / Directus / Grafana 等）在**应用层**做租户过滤，
而不是依赖 RLS。原因是本项目用**连接池 + APScheduler 后台任务**：
RLS 要求每个连接先 `SET LOCAL app.tenant_id`，而池里的连接被多个请求复用，
事务外设置不可靠、池内串号风险高。

应用层过滤与既有架构一致（`resolve_grant` 已经是应用层裁剪），可单测、
可代码评审、漏了能被测试抓住。

## default 租户

迁移时把存量数据全部归入 `default`（slug='default'）。
**它的存在是为了零回归**：升级后行为与单租户时代完全一致。
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 存量数据归属的租户 slug。升级后行为与单租户时代一致。
DEFAULT_TENANT_SLUG = "default"
DEFAULT_TENANT_NAME = "默认组织"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """一个组织。数据集、用户、授权、流程都归属某个租户。"""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # slug 用于 URL / X-Tenant-Id 头，全局唯一、ASCII
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
