"""S18: incremental refresh — policies re-pend done tasks once their interval has elapsed, the
``data`` probe cascades on a changed ``last_update``, ``bps seed refresh`` (DB tests need Postgres).
"""

import copy
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select, text, update
from typer.testing import CliRunner

from bps_fetcher import queue, refresh, worker
from bps_fetcher.cli import app
from bps_fetcher.db.schema import domain, task, variable
from bps_fetcher.handlers.data import data
from bps_fetcher.settings import get_settings
from bps_fetcher.worker import TaskContext

Body = Callable[[str], Any]
NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}
T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)  # when the seeded tasks "completed"
BPS_TZ_OFFSET = timedelta(hours=7)

runner = CliRunner()


# --- helpers -------------------------------------------------------------------------------------


def _done(
    engine: Engine,
    kind: str,
    params: dict[str, Any],
    *,
    at: datetime = T0,
    parent: int | None = None,
) -> int:
    """Insert a task that completed at ``at``."""
    with engine.begin() as conn:
        tid = queue.enqueue(conn, kind, params, parent_id=parent)
        assert tid is not None
        conn.execute(update(task).where(task.c.id == tid).values(status=queue.DONE, updated_at=at))
    return tid


def _status(engine: Engine, tid: int) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(task).where(task.c.id == tid)).one()


def _pending(engine: Engine, kind: str | None = None) -> list[dict[str, Any]]:
    q = select(task.c.params).where(task.c.status == queue.PENDING).order_by(task.c.id)
    if kind is not None:
        q = q.where(task.c.kind == kind)
    with engine.connect() as conn:
        return list(conn.execute(q).scalars())


def _refresh(engine: Engine, now: datetime, **kw: Any) -> dict[str, int]:
    with engine.begin() as conn:
        return refresh.refresh(conn, now=now, **kw)


def _domains(engine: Engine, *ids: str) -> None:
    with engine.begin() as conn:
        for d in ids:
            level = "pusat" if d == "0000" else ("prov" if d.endswith("00") else "kab")
            conn.execute(insert(domain).values(domain_id=d, name=d, level=level))


def _var(
    engine: Engine, var_id: int, last_update: datetime | None, domain_id: str = "0000"
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(variable).values(
                domain_id=domain_id, var_id=var_id, title=f"V{var_id}", last_update=last_update
            )
        )


def _bps_local(at: datetime) -> datetime:
    """``variable.last_update`` is BPS wall-clock time (naive, WIB)."""
    return (at + BPS_TZ_OFFSET).replace(tzinfo=None)


# --- indicators: daily ---------------------------------------------------------------------------


def test_indicators_rerun_daily_and_idempotent(db_engine: Engine) -> None:
    tid = _done(db_engine, "indicators", {"domain": "0000"})

    assert _refresh(db_engine, T0 + timedelta(hours=23))["indicators"] == 0
    assert _status(db_engine, tid).status == queue.DONE

    assert _refresh(db_engine, T0 + timedelta(hours=25))["indicators"] == 1
    row = _status(db_engine, tid)
    assert (row.status, row.attempts, row.last_error) == (queue.PENDING, 0, None)
    with db_engine.connect() as conn:
        due = conn.execute(text("SELECT next_run_at <= now() FROM task WHERE id = :i"), {"i": tid})
        assert due.scalar_one() is True

    # Running it twice in a row schedules nothing new.
    assert _refresh(db_engine, T0 + timedelta(hours=25)) == dict.fromkeys(refresh.POLICIES, 0)


def test_indicators_canonical_task_per_seeded_domain(db_engine: Engine) -> None:
    # Only run-labelled tasks (S14 style) exist: refresh adds one canonical task per domain.
    _done(db_engine, "indicators", {"domain": "3100", "run": "2026-10-01"})
    _done(db_engine, "indicators", {"domain": "3100", "run": "2026-10-02"})
    assert _refresh(db_engine, T0)["indicators"] == 1
    assert _pending(db_engine, "indicators") == [{"domain": "3100"}]


# --- trade: current + previous year, weekly ------------------------------------------------------


def _trade(
    year: int, chapters: str = "01;02", flow: int = 1, period_type: int = 2
) -> dict[str, Any]:
    return {"flow": flow, "period_type": period_type, "year": year, "chapters": chapters}


def test_trade_current_and_previous_year_weekly(db_engine: Engine) -> None:
    old = _done(db_engine, "trade", _trade(2024))
    _done(db_engine, "trade", _trade(2024, "03;04"))
    _done(db_engine, "trade", {**_trade(2023), "run": "x"})  # same combo, labelled

    now = datetime(2026, 10, 9, tzinfo=UTC)
    assert _refresh(db_engine, now)["trade"] == 4  # 2 batches x {2025, 2026}, all new
    assert sorted((p["year"], p["chapters"]) for p in _pending(db_engine, "trade")) == [
        (2025, "01;02"),
        (2025, "03;04"),
        (2026, "01;02"),
        (2026, "03;04"),
    ]
    assert _status(db_engine, old).status == queue.DONE  # 2024 is not refreshed any more
    assert _refresh(db_engine, now)["trade"] == 0

    with db_engine.begin() as conn:
        conn.execute(
            update(task).where(task.c.kind == "trade").values(status=queue.DONE, updated_at=now)
        )
    assert _refresh(db_engine, now + timedelta(days=6))["trade"] == 0
    assert _refresh(db_engine, now + timedelta(days=8))["trade"] == 4
    assert _status(db_engine, old).status == queue.DONE
    assert _refresh(db_engine, now + timedelta(days=8))["trade"] == 0


def test_trade_respects_earliest_year(db_engine: Engine) -> None:
    _done(db_engine, "trade", _trade(2014))
    assert _refresh(db_engine, datetime(2014, 6, 1, tzinfo=UTC))["trade"] == 0  # 2013 never
    assert _refresh(db_engine, datetime(2014, 6, 9, tzinfo=UTC))["trade"] == 0


# --- var re-list: weekly -------------------------------------------------------------------------


def test_var_list_weekly_and_domain_scope(db_engine: Engine) -> None:
    nat = _done(db_engine, "var_list", {"domain": "0000"})
    jkt = _done(db_engine, "var_list", {"domain": "3100", "limit_vars": 5})

    assert _refresh(db_engine, T0 + timedelta(days=6))["var_list"] == 0
    got = _refresh(db_engine, T0 + timedelta(days=8), domains=["0000"])
    assert got["var_list"] == 1
    assert (_status(db_engine, nat).status, _status(db_engine, jkt).status) == ("pending", "done")
    assert _refresh(db_engine, T0 + timedelta(days=8))["var_list"] == 1  # now 3100 too


def test_sources_select_policies(db_engine: Engine) -> None:
    _done(db_engine, "var_list", {"domain": "0000"})
    _done(db_engine, "indicators", {"domain": "0000"})
    got = _refresh(db_engine, T0 + timedelta(days=8), sources=["indicators"])
    assert got == {"indicators": 1}
    assert _pending(db_engine, "var_list") == []
    with pytest.raises(ValueError, match="source"):
        _refresh(db_engine, T0, sources=["nope"])


# --- data probes: latest window per var, interval by last_update age ----------------------------


@pytest.fixture
def probe_vars(db_engine: Engine) -> dict[str, int]:
    """Vars: hot (updated 10 days ago), warm (200 days), cold (3 years), never loaded (NULL)."""
    _domains(db_engine, "0000")
    _var(db_engine, 1, _bps_local(T0 - timedelta(days=10)))
    _var(db_engine, 2, _bps_local(T0 - timedelta(days=200)))
    _var(db_engine, 3, _bps_local(T0 - timedelta(days=3 * 365)))
    _var(db_engine, 4, None)
    ids: dict[str, int] = {}
    for th in ("117:119", "120:122", "123"):
        ids[f"1/{th}"] = _done(db_engine, "data", {"domain": "0000", "var": 1, "th": th})
    # var 2: a window split into single periods (S13) — the parent isn't a probe candidate.
    parent = _done(db_engine, "data", {"domain": "0000", "var": 2, "th": "124:126"})
    ids["2/124:126"] = parent
    for th in ("124", "125", "126"):
        ids[f"2/{th}"] = _done(
            db_engine, "data", {"domain": "0000", "var": 2, "th": th}, parent=parent
        )
    ids["2/121:123"] = _done(db_engine, "data", {"domain": "0000", "var": 2, "th": "121:123"})
    ids["3/100"] = _done(db_engine, "data", {"domain": "0000", "var": 3, "th": "100"})
    ids["4/90"] = _done(db_engine, "data", {"domain": "0000", "var": 4, "th": "90"})
    return ids


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, []),  # staggered (S19): before a full interval only some vars are due
        (8, ["1/123"]),
        (29, ["1/123", "2/126"]),
        (92, ["1/123", "2/126", "3/100", "4/90"]),
    ],
)
def test_data_probe_picks_latest_window_by_tier(
    db_engine: Engine, probe_vars: dict[str, int], days: int, expected: list[str]
) -> None:
    got = _refresh(db_engine, T0 + timedelta(days=days), sources=["dynamic"])
    assert got["data_probe"] == len(expected)
    pending = {
        name for name, tid in probe_vars.items() if _status(db_engine, tid).status == "pending"
    }
    assert pending == set(expected)
    assert _refresh(db_engine, T0 + timedelta(days=days), sources=["dynamic"])["data_probe"] == 0


def test_data_probe_skips_var_whose_latest_window_is_not_done(
    db_engine: Engine, probe_vars: dict[str, int]
) -> None:
    with db_engine.begin() as conn:
        conn.execute(update(task).where(task.c.id == probe_vars["1/123"]).values(status="dead"))
    got = _refresh(db_engine, T0 + timedelta(days=8), sources=["dynamic"])
    assert got["data_probe"] == 0
    assert _status(db_engine, probe_vars["1/120:122"]).status == "done"


def test_data_probe_domain_scope(db_engine: Engine, probe_vars: dict[str, int]) -> None:
    _domains(db_engine, "3100")
    _var(db_engine, 1, _bps_local(T0 - timedelta(days=10)), domain_id="3100")
    other = _done(db_engine, "data", {"domain": "3100", "var": 1, "th": "123"})
    got = _refresh(db_engine, T0 + timedelta(days=8), sources=["dynamic"], domains=["3100"])
    assert got["data_probe"] == 1
    assert _status(db_engine, other).status == "pending"
    assert _status(db_engine, probe_vars["1/123"]).status == "done"


# --- S19: probes are staggered across their interval ---------------------------------------------


def test_probe_phase_is_deterministic_spread_and_in_unit_range() -> None:
    phases = [refresh.probe_phase("3400", v) for v in range(1, 2001)]
    assert phases == [refresh.probe_phase("3400", v) for v in range(1, 2001)]
    assert all(0.0 <= p < 1.0 for p in phases)
    # roughly uniform: every tenth of the interval gets 10 % +- 3 % of the vars
    for decile in range(10):
        share = sum(decile / 10 <= p < (decile + 1) / 10 for p in phases) / len(phases)
        assert 0.07 <= share <= 0.13, (decile, share)
    assert refresh.probe_phase("3400", 7) != refresh.probe_phase("3401", 7)


def _bulk_hot_vars(engine: Engine, n: int, *, domain_id: str = "3400") -> dict[int, int]:
    """``n`` hot vars (updated 10 days before T0) whose single window completed at T0."""
    _domains(engine, domain_id)
    stamp = _bps_local(T0 - timedelta(days=10))
    ids: dict[int, int] = {}
    with engine.begin() as conn:
        conn.execute(
            insert(variable),
            [
                {"domain_id": domain_id, "var_id": v, "title": f"V{v}", "last_update": stamp}
                for v in range(1, n + 1)
            ],
        )
        for v in range(1, n + 1):
            tid = queue.enqueue(conn, "data", {"domain": domain_id, "var": v, "th": "120"})
            assert tid is not None
            ids[v] = tid
        conn.execute(update(task).values(status=queue.DONE, updated_at=T0))
    return ids


def _expected_due(domain_id: str, var_ids: range, done: datetime, now: datetime) -> set[int]:
    """Python model of the stagger: due once a phase boundary lies in ``(done, now]``."""
    every = refresh.PROBE_HOT_EVERY.total_seconds()
    out = set()
    for v in var_ids:
        phase = refresh.probe_phase(domain_id, v) * every
        if (now.timestamp() - phase) // every > (done.timestamp() - phase) // every:
            out.add(v)
    return out


def test_data_probes_are_staggered_across_the_interval(db_engine: Engine) -> None:
    n = 300
    ids = _bulk_hot_vars(db_engine, n)
    status = {tid: v for v, tid in ids.items()}

    def due_after(delta: timedelta) -> set[int]:
        with db_engine.connect() as conn:
            txn = conn.begin()
            refresh.refresh(conn, now=T0 + delta, sources=["dynamic"])
            got = conn.execute(
                select(task.c.id).where(task.c.status == queue.PENDING, task.c.kind == "data")
            ).scalars()
            due = {status[t] for t in got}
            txn.rollback()
        return due

    assert due_after(timedelta(0)) == set()
    one_day = due_after(timedelta(days=1))
    assert one_day == _expected_due("3400", range(1, n + 1), T0, T0 + timedelta(days=1))
    assert 0.07 * n <= len(one_day) <= 0.22 * n  # ~1/7 of the week, not all at once
    half = due_after(timedelta(days=3, hours=12))
    assert one_day < half
    assert 0.35 * n <= len(half) <= 0.65 * n
    assert due_after(PROBE_WEEK) == set(range(1, n + 1))  # never later than one interval


PROBE_WEEK = refresh.PROBE_HOT_EVERY


def test_staggered_probe_keeps_its_phase(db_engine: Engine) -> None:
    """A var probed at its boundary is next due exactly one interval later (no drift)."""
    ids = _bulk_hot_vars(db_engine, 1)
    every = PROBE_WEEK.total_seconds()
    phase = refresh.probe_phase("3400", 1) * every
    # The first boundary after T0:
    k = (T0.timestamp() - phase) // every + 1
    boundary = datetime.fromtimestamp(k * every + phase, UTC)
    assert T0 < boundary <= T0 + PROBE_WEEK

    second = timedelta(seconds=1)
    assert _refresh(db_engine, boundary - second, sources=["dynamic"])["data_probe"] == 0
    assert _refresh(db_engine, boundary + second, sources=["dynamic"])["data_probe"] == 1
    # The probe runs and completes a minute after the boundary.
    with db_engine.begin() as conn:
        conn.execute(
            update(task)
            .where(task.c.id == ids[1])
            .values(status=queue.DONE, updated_at=boundary + timedelta(minutes=1))
        )
    nxt = boundary + PROBE_WEEK
    assert _refresh(db_engine, nxt - second, sources=["dynamic"])["data_probe"] == 0
    assert _refresh(db_engine, nxt + second, sources=["dynamic"])["data_probe"] == 1


# --- cascade: a probe that sees a newer last_update re-runs the var -----------------------------


class FakeClient:
    def __init__(self, answer: Callable[[dict[str, Any]], Any]) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, **params: Any) -> Any:
        self.calls.append(params)
        return self.answer(params)


def _stamped(body: dict[str, Any], last_update: str) -> dict[str, Any]:
    out = copy.deepcopy(body)
    out["last_update"] = last_update
    return out


@pytest.fixture
def var_1804(db_engine: Engine) -> dict[str, int]:
    """Var 1804 crawled once (last_update 2020-03-26): th_list + two data windows, all done."""
    _domains(db_engine, "0000")
    _var(db_engine, 1804, datetime(2020, 3, 26))
    ids = {"th_list": _done(db_engine, "th_list", {"domain": "0000", "var": 1804})}
    for th in ("114:116", "117:119"):
        ids[th] = _done(
            db_engine, "data", {"domain": "0000", "var": 1804, "th": th}, parent=ids["th_list"]
        )
    return ids


async def _probe(engine: Engine, tid: int, body: dict[str, Any]) -> None:
    t = queue.Task(
        id=tid, kind="data", params=_status(engine, tid).params, attempts=0, parent_id=None
    )
    with engine.begin() as conn:
        await data(TaskContext(t, FakeClient(lambda _: body), conn))


async def test_newer_last_update_reruns_other_windows_and_th_list(
    db_engine: Engine, var_1804: dict[str, int], fixture_body: Body
) -> None:
    await _probe(
        db_engine,
        var_1804["117:119"],
        _stamped(fixture_body("data_0000_1804"), "2026-10-05 08:00:00"),
    )
    assert _status(db_engine, var_1804["th_list"]).status == "pending"
    assert _status(db_engine, var_1804["114:116"]).status == "pending"
    assert _status(db_engine, var_1804["117:119"]).status == "done"  # the probe itself


@pytest.mark.parametrize("stamp", ["2020-03-26 00:00:00", "2019-01-01 00:00:00"])
async def test_same_or_older_last_update_reruns_nothing(
    db_engine: Engine, var_1804: dict[str, int], fixture_body: Body, stamp: str
) -> None:
    await _probe(db_engine, var_1804["117:119"], _stamped(fixture_body("data_0000_1804"), stamp))
    assert _pending(db_engine) == []


async def test_first_load_of_a_var_reruns_nothing(
    db_engine: Engine, var_1804: dict[str, int], fixture_body: Body
) -> None:
    with db_engine.begin() as conn:
        conn.execute(update(variable).values(last_update=None))
    await _probe(db_engine, var_1804["117:119"], fixture_body("data_0000_1804"))
    assert _pending(db_engine) == []


async def test_refresh_end_to_end_new_th_fetches_only_new_window(
    db_engine: Engine, var_1804: dict[str, int], fixture_body: Body
) -> None:
    """Probe sees a newer stamp → th_list re-runs → only the new last window is a new task."""
    data_body = _stamped(fixture_body("data_0000_1804"), "2026-10-05 08:00:00")
    th_body = copy.deepcopy(fixture_body("th_0000_1804"))  # 113..119
    th_body["data"][1] = [{"th_id": i, "th": str(1900 + i)} for i in range(114, 121)]

    def answer(p: dict[str, Any]) -> Any:
        if p["model"] == "th":
            return th_body
        return data_body if p["th"] == "117:119" else NOT_AVAILABLE

    client = FakeClient(answer)
    got = _refresh(db_engine, T0 + timedelta(days=92), sources=["dynamic"])  # cold var
    assert got["data_probe"] == 1
    stats = await worker.run_worker(db_engine, client=client, idle_sleep=0.01)

    calls = [(c["model"], c.get("th")) for c in client.calls]
    assert calls[0] == ("data", "117:119")  # the probe
    assert sorted(calls[1:]) == [("data", "114:116"), ("data", "120"), ("th", None)]
    assert stats.failed == 0
    with db_engine.connect() as conn:
        windows = sorted(
            conn.execute(select(task.c.params["th"].astext).where(task.c.kind == "data")).scalars()
        )
        last_update = conn.execute(select(variable.c.last_update)).scalar_one()
    assert windows == ["114:116", "117:119", "120"]
    assert last_update == datetime(2026, 10, 5, 8)
    # Nothing left to do, and an immediate second refresh schedules nothing.
    assert _pending(db_engine) == []
    assert _refresh(db_engine, datetime.now(UTC)) == dict.fromkeys(refresh.POLICIES, 0)


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, db_url: str) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("BPS_API_KEY", "test-key-0123456789abcdef")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cli_seed_refresh(cli_env: None, db_engine: Engine) -> None:
    _done(db_engine, "indicators", {"domain": "0000"})
    _done(db_engine, "var_list", {"domain": "0000"})
    as_of = (T0 + timedelta(days=2)).isoformat()

    dry = runner.invoke(app, ["seed", "refresh", "--as-of", as_of, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "indicators" in dry.output
    assert "dry run" in dry.output.lower()
    assert _pending(db_engine) == []

    result = runner.invoke(app, ["seed", "refresh", "--as-of", as_of])
    assert result.exit_code == 0, result.output
    lines = [line.split() for line in result.output.splitlines()]
    assert ["indicators", "1"] in lines
    assert ["var_list", "0"] in lines
    assert _pending(db_engine) == [{"domain": "0000"}]

    again = runner.invoke(app, ["seed", "refresh", "--as-of", as_of])
    assert again.exit_code == 0, again.output
    assert "Nothing to refresh" in again.output

    scoped = runner.invoke(
        app,
        ["seed", "refresh", "--as-of", (T0 + timedelta(days=8)).isoformat(), "--source", "trade"],
    )
    assert scoped.exit_code == 0, scoped.output
    assert _pending(db_engine, "var_list") == []


@pytest.mark.parametrize(
    "args", [["--source", "nope"], ["--as-of", "yesterday"], ["--domain", "12"]]
)
def test_cli_seed_refresh_rejects_bad_options(
    cli_env: None, db_engine: Engine, args: list[str]
) -> None:
    result = runner.invoke(app, ["seed", "refresh", *args])
    assert result.exit_code != 0
