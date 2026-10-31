from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url)
    # PostgreSQL: check a pooled connection is alive before using it, so a database restart or an
    # idle timeout on the server doesn't surface as a failed request.
    return create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    with factory() as session:
        yield session


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    """For code that runs outside a request (background ingestion)."""
    return sessionmaker(bind=get_engine(database_url), expire_on_commit=False)
