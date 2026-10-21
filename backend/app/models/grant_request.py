"""权限申请单：让业务人员能申请、管理员审批，审批通过自动落成授权。

## 为什么需要申请流

原先授权只能由管理员手工建 `DatasetGrant`。三个问题：

1. 业务人员要等管理员**记得**给自己开权限；
2. 管理员说不清"这个权限当初为什么开"——没有理由留存；
3. 权限一旦开出就**永久有效**，转岗/离职后静默残留，审计最先查这个。

## 状态机

    pending ──approve──> approved（同时落成 DatasetGrant，带有效期）
    pending ──reject───► rejected（必须填理由）
    pending ──cancel────> cancelled（申请人自己撤销）

`approved` 是**终态**：申请单本身不再变化，生效性由落成的那条
`DatasetGrant.expires_at` 决定（到期自动 expired，见 `app/grants/expiry.py`）。
这样"申请"与"生效"是两个关注点，不会互相污染。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 申请单状态
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

# 允许的状态流转：{from: {to: 原因必填?}}
_TRANSITIONS: dict[str, dict[str, bool]] = {
    STATUS_PENDING: {
        STATUS_APPROVED: False,
        STATUS_REJECTED: True,
        STATUS_CANCELLED: False,
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GrantRequest(Base):
    """一条权限申请。审批通过后落成 `DatasetGrant`。"""

    __tablename__ = "grant_requests"
    __table_args__ = (Index("ix_grant_req_status_tenant", "status", "tenant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)

    # 申请人（不是审批人）
    applicant_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 审批人（approve/reject 时回填）
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PENDING, index=True
    )
    # 申请理由（必填）—— 留存"这个权限当初为什么开"
    reason: Mapped[str] = mapped_column(Text)
    # 审批意见（reject 时必填）
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 申请时声明的期望权限（审批时可改，落成 Grant 以最终值为准）
    requested_row_rules: Mapped[list] = mapped_column(JSON, default=list)
    requested_field_whitelist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 申请的有效期天数（空 = 永久）；落成 Grant 时换算成 expires_at
    requested_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 审批通过后落成的那条授权（便于从申请单反查；一条申请对应一条授权）
    granted_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_grants.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def can_transition(self, to_status: str) -> tuple[bool, str]:
        """校验状态流转。返回 (是否允许, 不允许时的原因)。"""
        allowed = _TRANSITIONS.get(self.status, {})
        if to_status not in allowed:
            current = self.status
            if to_status == current:
                return False, f"申请单已是 {current} 状态，不能重复操作"
            return False, f"申请单当前是 {current}，不能流转到 {to_status}"
        return True, ""
