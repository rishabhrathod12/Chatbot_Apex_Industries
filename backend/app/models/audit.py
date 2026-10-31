from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import utcnow


class AuditLog(Base):
    """Who did what to what, and whether it was allowed. Never holds document text, questions,
    answers, passwords or tokens."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_user_time", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(48))
    outcome: Mapped[str] = mapped_column(String(16))  # ok | denied | failed
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(64), nullable=True)  # a short code
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
