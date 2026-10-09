"""S5: schema + Alembic migrations (needs Postgres; skipped when unreachable)."""

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from bps_fetcher.db.migrate import alembic_config, downgrade, upgrade
from bps_fetcher.db.schema import domain, metadata, raw_response, task

# Every table the migrations create; grows with each slice that adds one.
ALL_TABLES = {
    "raw_response",
    "task",
    "domain",
    "variable",
    "period",
    "dim_vervar",
    "dim_turvar",
    "dim_turth",
    "observation",
}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


# --- config (no DB) ------------------------------------------------------------------------------


def test_alembic_config_uses_explicit_url() -> None:
    cfg = alembic_config("postgresql+psycopg://u:p@h:1/x")
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql+psycopg://u:p@h:1/x"


def test_alembic_config_defaults_to_database_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env:env@envhost:5432/envdb")
    cfg = alembic_config()
    assert cfg.get_main_option("sqlalchemy.url") == (
        "postgresql+psycopg://env:env@envhost:5432/envdb"
    )


def test_alembic_config_works_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BPS_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir("/")  # no .env here
    cfg = alembic_config()
    assert cfg.get_main_option("sqlalchemy.url") == (
        "postgresql+psycopg://bps:bps@localhost:5432/bps"
    )


def test_metadata_defines_all_tables() -> None:
    assert set(metadata.tables) == ALL_TABLES


# --- migrations ----------------------------------------------------------------------------------


def test_upgrade_from_empty_creates_all_tables(empty_db: str) -> None:
    assert _tables(empty_db) == set()
    upgrade(empty_db, "head")
    assert _tables(empty_db) == ALL_TABLES | {"alembic_version"}


def test_downgrade_removes_all_tables(empty_db: str) -> None:
    upgrade(empty_db, "head")
    downgrade(empty_db, "base")
    assert _tables(empty_db) - {"alembic_version"} == set()


def test_upgrade_downgrade_upgrade_roundtrip(empty_db: str) -> None:
    upgrade(empty_db, "head")
    downgrade(empty_db, "base")
    upgrade(empty_db, "head")
    assert _tables(empty_db) >= ALL_TABLES


def test_migrations_match_metadata(db_engine: Engine) -> None:
    """Autogenerate finds nothing to do: the migration and schema.py agree."""
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


# --- constraints ---------------------------------------------------------------------------------


def _task(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": "var_list", "params": {"domain": "0000"}, "params_hash": "h1"}
    row.update(overrides)
    return row


def test_task_defaults(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        task_id = conn.execute(insert(task).values(_task()).returning(task.c.id)).scalar_one()
        row = conn.execute(select(task).where(task.c.id == task_id)).mappings().one()
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["next_run_at"] is not None
    assert row["params"] == {"domain": "0000"}


def test_task_unique_kind_params_hash(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(insert(task).values(_task()))
        conn.execute(insert(task).values(_task(kind="th_list")))  # same hash, other kind: ok
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(insert(task).values(_task()))


def test_task_parent_fk(db_engine: Engine) -> None:
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(insert(task).values(_task(parent_id=999_999)))


def test_raw_response_links_to_task(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        task_id = conn.execute(insert(task).values(_task()).returning(task.c.id)).scalar_one()
        conn.execute(
            insert(raw_response).values(
                task_id=task_id,
                endpoint="list",
                params={"model": "var", "page": 1},
                body={"status": "OK"},
                sha256="0" * 64,
            )
        )
        row = conn.execute(select(raw_response)).mappings().one()
    assert row["task_id"] == task_id
    assert row["body"] == {"status": "OK"}
    assert row["fetched_at"] is not None


def test_domain_primary_key(db_engine: Engine) -> None:
    values = {
        "domain_id": "0000",
        "name": "Pusat",
        "url": "https://www.bps.go.id",
        "level": "pusat",
    }
    with db_engine.begin() as conn:
        conn.execute(insert(domain).values(values))
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(insert(domain).values(values))


def test_domain_level_checked(db_engine: Engine) -> None:
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(insert(domain).values(domain_id="9999", name="X", level="planet"))


def test_db_engine_fixture_starts_clean(db_engine: Engine) -> None:
    """Rows written by earlier tests are truncated."""
    with db_engine.connect() as conn:
        assert conn.execute(select(task)).first() is None
        assert conn.execute(select(domain)).first() is None
