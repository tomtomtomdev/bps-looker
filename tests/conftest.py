import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from bps_fetcher.db.migrate import downgrade, upgrade
from bps_fetcher.db.schema import metadata

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> dict[str, Any]:
    """Return a recorded fixture (``name``, ``request``, ``status_code``, ``body``...)."""
    data: dict[str, Any] = json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def fixture_body() -> Callable[[str], Any]:
    """Factory returning the recorded response body of a fixture by name."""
    return lambda name: read_fixture(name)["body"]


# --- Postgres -----------------------------------------------------------------------------------
# DB tests read TEST_DATABASE_URL (default: local bps_test) and are skipped when it is unreachable.
# Never point this at a database whose contents you care about: fixtures drop every table.

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://bps:bps@localhost:5432/bps_test"


@pytest.fixture(scope="session")
def db_url() -> str:
    """URL of the test database; skips the test when Postgres isn't reachable."""
    url = os.environ.get("TEST_DATABASE_URL") or DEFAULT_TEST_DATABASE_URL
    probe = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with probe.connect():
            pass
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable at TEST_DATABASE_URL ({type(exc).__name__})")
    finally:
        probe.dispose()
    return url


def reset_db(url: str) -> None:
    """Downgrade to base and drop Alembic's version table: an empty database."""
    downgrade(url, "base")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def _db_session_cleanup(db_url: str) -> Iterator[None]:
    yield
    reset_db(db_url)


@pytest.fixture
def empty_db(db_url: str, _db_session_cleanup: None) -> Iterator[str]:
    """An empty test database (no tables); yields its URL and empties it again afterwards."""
    reset_db(db_url)
    yield db_url
    reset_db(db_url)


@pytest.fixture
def db_engine(db_url: str, _db_session_cleanup: None) -> Iterator[Engine]:
    """Engine on the test database migrated to head; all tables are truncated after the test."""
    upgrade(db_url, "head")
    engine = create_engine(db_url)
    try:
        yield engine
        tables = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()
