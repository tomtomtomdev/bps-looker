"""S21: container entrypoint — migrate on start, then exec the given command."""

from collections.abc import Sequence
from typing import Any

import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from bps_fetcher import entrypoint
from bps_fetcher.db.migrate import alembic_config, upgrade


class Recorder:
    """Stands in for ``upgrade`` / ``os.execvp`` / ``time.sleep`` and logs the call order."""

    def __init__(self, upgrade_failures: int = 0) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.upgrade_failures = upgrade_failures

    def upgrade(self, url: str | None = None, revision: str = "head") -> None:
        self.calls.append(("upgrade", revision))
        if self.upgrade_failures:
            self.upgrade_failures -= 1
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def execvp(self, file: str, args: Sequence[str]) -> None:
        self.calls.append(("exec", list(args)))
        assert file == args[0]

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(entrypoint, "upgrade", r.upgrade)
    monkeypatch.setattr("os.execvp", r.execvp)
    monkeypatch.setattr("time.sleep", r.sleep)
    monkeypatch.delenv("BPS_MIGRATE_ON_START", raising=False)
    return r


def test_migrates_then_execs_the_command(rec: Recorder) -> None:
    entrypoint.main(["bps", "status", "--max-dead", "0"])
    assert rec.calls == [("upgrade", "head"), ("exec", ["bps", "status", "--max-dead", "0"])]


def test_no_command_runs_default(rec: Recorder) -> None:
    entrypoint.main([])
    assert rec.calls == [("upgrade", "head"), ("exec", list(entrypoint.DEFAULT_COMMAND))]


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
def test_migration_can_be_disabled(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("BPS_MIGRATE_ON_START", value)
    entrypoint.main(["bps", "--help"])
    assert rec.calls == [("exec", ["bps", "--help"])]


def test_retries_while_db_is_unreachable(rec: Recorder) -> None:
    rec.upgrade_failures = 2
    entrypoint.main(["bps", "work"], attempts=5, delay=0.5)
    assert rec.calls == [
        ("upgrade", "head"),
        ("sleep", 0.5),
        ("upgrade", "head"),
        ("sleep", 0.5),
        ("upgrade", "head"),
        ("exec", ["bps", "work"]),
    ]


def test_gives_up_without_running_the_command(
    rec: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    rec.upgrade_failures = 10
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["bps", "work"], attempts=3, delay=0)
    assert exc.value.code == entrypoint.MIGRATE_FAILED_EXIT
    assert [c[0] for c in rec.calls] == ["upgrade", "sleep", "upgrade", "sleep", "upgrade"]
    assert "migration failed" in capsys.readouterr().err


def test_migrates_a_fresh_database(
    empty_db: str, monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """Against real Postgres: an empty DB is at head before the command is exec'd."""
    monkeypatch.setattr(entrypoint, "upgrade", upgrade)
    monkeypatch.setenv("DATABASE_URL", empty_db)
    entrypoint.main(["bps", "status"])
    assert rec.calls == [("exec", ["bps", "status"])]
    head = ScriptDirectory.from_config(alembic_config("postgresql://unused")).get_current_head()
    engine = create_engine(empty_db)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == head
    finally:
        engine.dispose()
