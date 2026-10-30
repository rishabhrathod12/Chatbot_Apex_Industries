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


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    with factory() as session:
        yield session
