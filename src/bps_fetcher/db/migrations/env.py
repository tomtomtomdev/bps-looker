"""Alembic environment: online/offline migrations against ``bps_fetcher.db.schema.metadata``."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from bps_fetcher.db.migrate import database_url
from bps_fetcher.db.schema import metadata

config = context.config
if config.config_file_name is not None:  # CLI run via alembic.ini; not when called from code
    fileConfig(config.config_file_name, disable_existing_loggers=False)
url = config.get_main_option("sqlalchemy.url") or database_url()


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
