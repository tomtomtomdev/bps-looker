"""Run Alembic migrations programmatically (tests, CLI, container start).

The URL comes from the argument, else ``DATABASE_URL`` (env / ``.env``) via
:class:`~bps_fetcher.settings.DatabaseSettings` — never hardcoded, never needs the API key.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

from bps_fetcher.settings import DatabaseSettings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def database_url() -> str:
    return DatabaseSettings().database_url


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # ConfigParser interpolation: a literal % (e.g. URL-encoded password) must be doubled.
    cfg.set_main_option("sqlalchemy.url", (url or database_url()).replace("%", "%%"))
    return cfg


def upgrade(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
