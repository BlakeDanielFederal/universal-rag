"""Programmatic Alembic entry points (used by `urag db-init` / `urag migrate`).

Builds an Alembic Config pointed at the repo's migrations/ and the app's DB URL,
so migrations run identically whether invoked via the CLI or the `alembic` tool.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from universal_rag.config import get_settings

# .../src/universal_rag/db/migrate.py -> repo root
_ROOT = Path(__file__).resolve().parents[3]


def alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url)
    return cfg


def upgrade(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def downgrade(revision: str) -> None:
    command.downgrade(alembic_config(), revision)


def current() -> None:
    command.current(alembic_config(), verbose=True)
