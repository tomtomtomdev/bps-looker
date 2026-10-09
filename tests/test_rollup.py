"""U6: ``mv_trade_rollup`` refresh — ``refresh_trade_rollup``, the worker's after-task refresher
and ``bps refresh-views``. DB tests need Postgres."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typer.testing import CliRunner

from bps_fetcher import queue, worker
from bps_fetcher.cli import app
from bps_fetcher.db.schema import hs_chapter, trade_flow
from bps_fetcher.rollup import ROLLUP_KINDS, refresh_trade_rollup, trade_refresher
from bps_fetcher.settings import get_settings
from bps_fetcher.worker import HandlerResult, Refresher, TaskContext


def _rollup(engine: Engine) -> dict[tuple[str, str], Decimal]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT grp, key, value_usd FROM mv_trade_rollup WHERE year = 2024")
        ).all()
    return {(r.grp, r.key): r.value_usd for r in rows}


def _insert(engine: Engine, value: str, country: str = "CHINA") -> None:
    with engine.begin() as conn:
        conn.execute(
            pg_insert(hs_chapter)
            .values(hs2="27", description="Fuels", source_flow=1, source_year=2024)
            .on_conflict_do_nothing()
        )
        conn.execute(
            insert(trade_flow).values(
                flow=1, period_type=2, year=2024, month=0, hs2="27", port="", country=country,
                value_usd=Decimal(value), netweight_kg=None,
            )
        )  # fmt: skip


@pytest.mark.parametrize("concurrently", [True, False])
def test_refresh_picks_up_new_rows(db_engine: Engine, concurrently: bool) -> None:
    refresh_trade_rollup(db_engine, concurrently=False)
    assert _rollup(db_engine) == {}
    _insert(db_engine, "10")
    _insert(db_engine, "5", country="")
    assert _rollup(db_engine) == {}  # stale until refreshed
    refresh_trade_rollup(db_engine, concurrently=concurrently)
    assert _rollup(db_engine) == {
        ("total", ""): Decimal("15"),
        ("hs2", "27"): Decimal("15"),
        ("country", "CHINA"): Decimal("10"),
        ("country", ""): Decimal("5"),
        ("port", ""): Decimal("15"),
    }


# --- worker refresher ----------------------------------------------------------------------------


async def _noop(ctx: TaskContext) -> HandlerResult:
    return HandlerResult()


def _seed(engine: Engine, kind: str, n: int) -> None:
    with engine.begin() as conn:
        for i in range(n):
            queue.enqueue(conn, kind, {"i": i})


async def _run(engine: Engine, refresher: Refresher, **kw: Any) -> worker.Stats:
    return await worker.run_worker(
        engine,
        client=object(),  # type: ignore[arg-type]  # handlers never call it
        handlers={"trade": _noop, "data": _noop},
        idle_sleep=0.01,
        refreshers=[refresher],
        **kw,
    )


async def test_refresher_not_called_without_matching_tasks(db_engine: Engine) -> None:
    calls: list[Engine] = []
    _seed(db_engine, "data", 3)
    stats = await _run(db_engine, Refresher(frozenset({"trade"}), calls.append, interval=3600))
    assert stats.done == 3
    assert calls == []


async def test_refresher_runs_once_when_the_worker_stops(db_engine: Engine) -> None:
    calls: list[Engine] = []
    _seed(db_engine, "trade", 3)
    _seed(db_engine, "data", 2)
    await _run(db_engine, Refresher(frozenset({"trade"}), calls.append, interval=3600))
    assert calls == [db_engine]


async def test_refresher_runs_during_the_run_after_the_interval(db_engine: Engine) -> None:
    calls: list[Engine] = []
    _seed(db_engine, "trade", 3)
    await _run(db_engine, Refresher(frozenset({"trade"}), calls.append, interval=0))
    assert len(calls) == 3  # after each trade task; nothing left dirty at the end


async def test_refresher_error_is_logged_not_raised(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(engine: Engine) -> None:
        raise RuntimeError("refresh failed")

    _seed(db_engine, "trade", 1)
    stats = await _run(db_engine, Refresher(frozenset({"trade"}), boom))
    assert stats.done == 1
    assert "refresh failed" in caplog.text


async def test_trade_refresher_refreshes_the_rollup(db_engine: Engine) -> None:
    refresh_trade_rollup(db_engine, concurrently=False)
    assert "trade" in ROLLUP_KINDS

    async def load(ctx: TaskContext) -> HandlerResult:
        ctx.conn.execute(
            insert(hs_chapter).values(
                hs2="27", description="Fuels", source_flow=1, source_year=2024
            )
        )
        ctx.conn.execute(
            insert(trade_flow).values(
                flow=1, period_type=2, year=2024, month=0, hs2="27", port="", country="CHINA",
                value_usd=Decimal("7"),
            )
        )  # fmt: skip
        return HandlerResult()

    _seed(db_engine, "trade", 1)
    await worker.run_worker(
        db_engine,
        client=object(),  # type: ignore[arg-type]
        handlers={"trade": load},
        idle_sleep=0.01,
        refreshers=[trade_refresher()],
    )
    assert _rollup(db_engine)[("total", "")] == Decimal("7")


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_refresh_views(db_engine: Engine, db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    refresh_trade_rollup(db_engine, concurrently=False)
    _insert(db_engine, "3")
    result = CliRunner().invoke(app, ["refresh-views"])
    assert result.exit_code == 0, result.output
    assert "mv_trade_rollup" in result.output
    assert _rollup(db_engine)[("total", "")] == Decimal("3")
