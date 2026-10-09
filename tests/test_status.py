"""S20: ``bps status`` — task counts, dead tasks, table sizes, last run per source, alert exit code
(DB tests need Postgres)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from bps_fetcher import status as status_mod
from bps_fetcher.cli import app
from bps_fetcher.settings import get_settings

runner = CliRunner()
FAKE_KEY = "test-key-0123456789abcdef"
T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, db_url: str) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _task(
    engine: Engine,
    kind: str,
    status: str,
    *,
    n: int = 1,
    error: str | None = None,
    updated_at: datetime = T0,
    next_run_at: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        for _ in range(n):
            conn.execute(
                text(
                    "INSERT INTO task (kind, params, params_hash, status, last_error, updated_at,"
                    " next_run_at) VALUES (:k, '{}', md5(random()::text), :s, :e, :u,"
                    " COALESCE(:nr, now()))"
                ),
                {"k": kind, "s": status, "e": error, "u": updated_at, "nr": next_run_at},
            )


def _seed(engine: Engine) -> None:
    _task(engine, "data", "done", n=3, updated_at=T0)
    _task(engine, "th_list", "done", updated_at=T0 + timedelta(hours=1))
    _task(engine, "indicators", "done", updated_at=T0 - timedelta(days=1))
    _task(engine, "data", "dead", error="BpsApiError: boom https://x/?key=SECRETKEY123&a=1")
    _task(engine, "trade", "dead", error="x" * 5000)
    _task(engine, "data", "pending", error="Timeout", next_run_at=T0 + timedelta(days=3650))
    _task(engine, "var_list", "pending")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO domain (domain_id, name, level) VALUES ('0000', 'Indonesia', 'pusat')"
            )
        )


# --- collect -------------------------------------------------------------------------------------


def test_collect_status(db_engine: Engine) -> None:
    _seed(db_engine)
    with db_engine.connect() as conn:
        s = status_mod.collect(conn, dead_limit=10)

    counts = {(c.kind, c.status): c.count for c in s.tasks}
    assert counts[("data", "done")] == 3
    assert counts[("data", "dead")] == 1
    assert counts[("trade", "dead")] == 1
    assert counts[("data", "pending")] == 1
    assert counts[("var_list", "pending")] == 1
    assert s.dead_total == 2
    assert s.pending_not_due == 1
    assert s.pending_retrying == 1  # pending with a last_error (in retry backoff)

    errors = {d.kind: d.last_error or "" for d in s.dead}
    assert "SECRETKEY123" not in errors["data"]
    assert "key=***" in errors["data"]
    assert len(errors["trade"]) <= status_mod.ERROR_PREVIEW_LEN + 1

    rows = {t.table: t for t in s.tables}
    assert rows["task"].rows == 9
    assert not rows["task"].estimate
    assert rows["domain"].rows == 1
    assert {"observation", "trade_flow", "raw_response"} <= rows.keys()

    last = {r.source: r.last_success for r in s.sources}
    assert last["dynamic"] == T0 + timedelta(hours=1)
    assert last["indicators"] == T0 - timedelta(days=1)
    assert last["trade"] is None


def test_dead_list_is_limited(db_engine: Engine) -> None:
    _task(db_engine, "data", "dead", n=5, error="boom")
    with db_engine.connect() as conn:
        s = status_mod.collect(conn, dead_limit=2)
    assert s.dead_total == 5
    assert len(s.dead) == 2


def test_big_tables_use_estimates(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(text("ANALYZE task"))
    with db_engine.connect() as conn:
        rows = status_mod.table_rows(conn, exact_below=0)
    # threshold 0: every analyzed table is reported as an estimate
    assert all(t.estimate for t in rows if t.table == "task")


# --- CLI -----------------------------------------------------------------------------------------


def test_status_cli_text(cli_env: None, db_engine: Engine) -> None:
    _seed(db_engine)
    result = runner.invoke(app, ["status", "--max-dead", "5"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "dead" in out.lower()
    assert "BpsApiError: boom" in out
    assert "SECRETKEY123" not in out
    assert "observation" in out
    for source in ("dynamic", "indicators", "trade"):
        assert source in out
    assert "not due yet" in out


def test_status_exits_nonzero_when_dead_exceeds_threshold(cli_env: None, db_engine: Engine) -> None:
    _seed(db_engine)  # 2 dead
    assert runner.invoke(app, ["status"]).exit_code == status_mod.ALERT_EXIT
    assert runner.invoke(app, ["status", "--max-dead", "1"]).exit_code == status_mod.ALERT_EXIT
    assert runner.invoke(app, ["status", "--max-dead", "2"]).exit_code == 0


def test_status_json(cli_env: None, db_engine: Engine) -> None:
    _seed(db_engine)
    result = runner.invoke(app, ["status", "--json", "--max-dead", "9"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["dead_total"] == 2
    assert {"kind": "data", "status": "done", "count": 3} in doc["tasks"]
    assert any(t["table"] == "observation" for t in doc["tables"])
    src = {s["source"]: s["last_success"] for s in doc["sources"]}
    assert src["trade"] is None
    assert datetime.fromisoformat(src["dynamic"]) == T0 + timedelta(hours=1)
    assert "SECRETKEY123" not in result.output


def test_status_json_alert_still_prints(cli_env: None, db_engine: Engine) -> None:
    _seed(db_engine)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == status_mod.ALERT_EXIT
    assert json.loads(result.stdout)["dead_total"] == 2
