"""Agent 运行时记录：一次运行、运行中的每个步骤、以及产出的交付物。

用途：
- `AgentRun`   —— 一次 Agent 运行（目标、触发方式、状态、耗时、token）；
- `AgentStep`  —— 运行中的每一步（规划/工具调用/反思），用于回放与排障；
- `AgentArtifact` —— 产出的简报/告警/待办草案，供人确认后落地。

这些表只做记录，不参与权限裁剪；真正的取数仍走 `app/access/` 下的裁剪器。
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def new_run_id() -> str:
    """运行标识：`run_` + 32 位十六进制，无序但不暴露自增。"""
    return f"run_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), unique=True, default=new_run_id)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(16), default="chat")  # chat/monitor/mcp
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/ok/failed/aborted
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_agent_steps_run_seq", "run_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    node: Mapped[str] = mapped_column(String(32))  # plan/act/reflect/answer/clarify
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok/failed/retried
    step_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    kind: Mapped[str] = mapped_column(String(16))  # brief/alert/todo
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
