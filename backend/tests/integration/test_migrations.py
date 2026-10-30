from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

BACKEND = Path(__file__).resolve().parents[2]


def test_upgrade_and_downgrade_on_empty_database(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")
    engine = create_engine(url)
    assert "documents" in inspect(engine).get_table_names()

    command.downgrade(cfg, "base")
    assert "documents" not in inspect(engine).get_table_names()
    engine.dispose()


def test_upgrade_creates_the_missing_database_folder(tmp_path: Path) -> None:
    """A fresh checkout has no storage/ folder, and `alembic upgrade head` must still work."""
    url = f"sqlite:///{tmp_path / 'does' / 'not' / 'exist' / 'app.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    assert {"documents", "conversations", "messages"} <= set(inspect(engine).get_table_names())
    engine.dispose()
