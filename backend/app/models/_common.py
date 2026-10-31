"""Small defaults shared by every table."""

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """A fresh 32-character id for a primary key."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """The current time in UTC, for created/updated columns."""
    return datetime.now(UTC)
