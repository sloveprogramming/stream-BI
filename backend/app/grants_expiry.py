"""到期授权自动过期：定时把已过 expires_at 的 active 授权置为 expired。

## 为什么需要这个定时任务

`resolve_grant` 已经在**读的时候**判过期（`active_grants_query` 带
`expires_at > now`），所以功能上不跑这个任务也不会越权。

但**管理界面要显示"已过期"**，而且审计要能追溯"什么时候过期的"。
只靠读时过滤的话，`status` 永远是 `active`，管理员看到的是
"这条授权还有效"—— 与事实不符。

所以这个任务是**把事实写进状态**，不是安全边界（安全边界在读时那道）。
"""
from __future__ import annotations

import logging

from app.grants import GRANT_EXPIRED, mark_expired

logger = logging.getLogger(__name__)

JOB_PREFIX = "grant_expiry"


def expire_due_grants(session_factory) -> list[int]:
    """把到期授权置 expired，返回受影响的授权 id。"""
    with session_factory() as session:
        ids = mark_expired(session)
    if ids:
        logger.info("授权到期自动过期：%s 条 -> %s", len(ids), ids)
    return ids


def register_job(scheduler, session_factory) -> None:
    """挂一个每小时跑的到期任务（与同步/报表/监控同一套调度器）。"""
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        expire_due_grants,
        IntervalTrigger(hours=1, timezone="Asia/Hong_Kong"),
        args=[session_factory],
        id=JOB_PREFIX,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
