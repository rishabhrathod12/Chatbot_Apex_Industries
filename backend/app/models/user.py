from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models._common import new_id, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    # What people type to sign in (the email works too). Lower-case, unique.
    login_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    # True after an admin creates or resets the account: the person must pick their own password.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(String(24))  # super_admin | department_manager | employee
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    departments: Mapped[list["UserDepartment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def department_codes(self) -> frozenset[str]:
        return frozenset(d.department for d in self.departments)


class UserDepartment(Base):
    __tablename__ = "user_departments"
    __table_args__ = (UniqueConstraint("user_id", "department", name="uq_user_department"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    department: Mapped[str] = mapped_column(String(32))  # canonical code

    user: Mapped[User] = relationship(back_populates="departments")


class RefreshToken(Base):
    """A login session that can be renewed. Only the hash of the token is stored."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
