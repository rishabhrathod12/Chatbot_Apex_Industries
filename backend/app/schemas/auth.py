import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, model_validator

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
    """Sign in with a Login ID or an email. `email` is the older spelling and still works."""

    login_id: str | None = Field(default=None, min_length=1, max_length=254)
    email: Email | None = None
    password: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _needs_an_identifier(self) -> "LoginRequest":
        if not (self.login_id or self.email):
            raise ValueError("login_id is required")
        return self

    @property
    def identifier(self) -> str:
        return (self.login_id or self.email or "").strip().lower()


class SetupStatus(BaseModel):
    setup_required: bool  # no user exists yet
    token_required: bool  # the person must also type the SETUP_TOKEN from .env
    available: bool  # false in production until SETUP_TOKEN is configured


class SetupRequest(BaseModel):
    email: Email
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)  # length rule comes from settings
    setup_token: str = Field(default="", max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class PrincipalOut(BaseModel):
    user_id: str
    login_id: str = ""
    email: str
    full_name: str
    role: Role
    departments: list[str]
    must_change_password: bool = False  # the app must show "choose a new password" first


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: PrincipalOut


class UserOut(BaseModel):
    id: str
    login_id: str
    email: str
    full_name: str
    role: Role
    departments: list[str]
    is_active: bool
    created_at: datetime
    must_change_password: bool = False


class UserCreated(UserOut):
    """The reply to creating an account. The temporary password is in this one response only
    (and only if the server generated it); it is never stored, logged or shown again."""

    temporary_password: str | None = None


class UserCreate(BaseModel):
    email: Email
    full_name: str = Field(min_length=1, max_length=120)
    # Leave out to have the server generate a temporary password (admin creation only).
    # The length rule comes from settings.
    password: str | None = Field(default=None, min_length=1, max_length=256)
    # Leave out to have one made from the name.
    login_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{3,32}$")
    role: Role
    departments: DepartmentList = []


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    departments: DepartmentList | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    """Leave `password` out to have the server generate a temporary one."""

    password: str | None = Field(default=None, min_length=1, max_length=256)


class PasswordResetResult(BaseModel):
    temporary_password: str | None = None  # only when generated, shown this once


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
