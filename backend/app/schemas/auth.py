import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.core.departments import DEPARTMENTS
from app.core.permissions import Role

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.match(value):
        raise ValueError("not a valid email address")
    return value


def _departments(values: list[str]) -> list[str]:
    cleaned = sorted({v.strip().lower() for v in values})
    unknown = [v for v in cleaned if v not in DEPARTMENTS]
    if unknown:
        raise ValueError("unknown department")
    return cleaned


Email = Annotated[str, Field(max_length=254), AfterValidator(_email)]
DepartmentList = Annotated[
    list[str], Field(max_length=len(DEPARTMENTS)), AfterValidator(_departments)
]


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class PrincipalOut(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: Role
    departments: list[str]


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: PrincipalOut


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: Role
    departments: list[str]
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    email: Email
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)  # length rule comes from settings
    role: Role
    departments: DepartmentList = []


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    departments: DepartmentList | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=256)
