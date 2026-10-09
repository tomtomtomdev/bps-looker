"""S7: worker runner (fake handlers + fake client; DB tests skip without Postgres)."""

import asyncio
import hashlib
import json
from typing import Any

import pytest
from sqlalchemy import Engine, func, select

from bps_fetcher import queue, worker
from bps_fetcher.db.schema import domain, raw_response, task
from bps_fetcher.worker import Child, HandlerResult, RawResponse, TaskContext

SECRET = "SUPERSECRETKEY42"


class FakeClient:
    """Stands in for BpsClient: records calls, returns canned bodies (never touches the network)."""

    def __init__(self, bodies: dict[str, dict[str, Any]] | None = None) -> None:
        self.bodies = bodies or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        return self.bodies.get(path, {"status": "OK", "data": []})


def _seed(engine: Engine, kind: str, *params: dict[str, Any]) -> list[int]:
    with engine.begin() as conn:
        ids = [queue.enqueue(conn, kind, p) for p in params]
    return [i for i in ids if i is not None]


def _tasks(engine: Engine) -> dict[int, Any]:
    with engine.connect() as conn:
        return {r.id: r for r in conn.execute(select(task)).all()}


def _raw(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(raw_response).order_by(raw_response.c.id)).all())


def _sha(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _run(engine: Engine, handlers: dict[str, worker.Handler], **kw: Any) -> worker.Stats:
    kw.setdefault("client", FakeClient())
    kw.setdefault("idle_sleep", 0.01)
    return await worker.run_worker(engine, handlers=handlers, **kw)


# --- happy path ----------------------------------------------------------------------------------


async def test_task_is_handled_raw_stored_children_enqueued_and_done(db_engine: Engine) -> None:
    body = {"status": "OK", "data": [{"page": 1, "pages": 1}, [{"domain_id": "0000"}]]}
    client = FakeClient({"domain": body})
    seen: list[dict[str, Any]] = []

    async def domains(ctx: TaskContext) -> HandlerResult:
        seen.append(ctx.params)
        got = await ctx.client.get("domain", type="all")
        return HandlerResult(
            raw=[RawResponse("domain", {"type": "all"}, got)],
            children=[Child("var_list", {"domain": "0000"}), Child("var_list", {"domain": "1100"})],
        )

    (parent,) = _seed(db_engine, "domains", {"type": "all"})
    stats = await _run(db_engine, {"domains": domains}, client=client, kinds=["domains"])

    assert seen == [{"type": "all"}]
    assert client.calls == [("domain", {"type": "all"})]
    assert stats.done == 1
    assert stats.failed == 0
    rows = _tasks(db_engine)
    assert rows[parent].status == queue.DONE
    children = sorted((r for r in rows.values() if r.kind == "var_list"), key=lambda r: r.id)
    assert [c.params for c in children] == [{"domain": "0000"}, {"domain": "1100"}]
    assert {c.parent_id for c in children} == {parent}
    assert {c.status for c in children} == {queue.PENDING}  # kinds filter: not run
    (raw,) = _raw(db_engine)
    assert raw.task_id == parent
    assert raw.endpoint == "domain"
    assert raw.params == {"type": "all"}
    assert raw.body == body
    assert raw.sha256 == _sha(body)


async def test_handler_db_writes_commit_with_the_task(db_engine: Engine) -> None:
    async def write(ctx: TaskContext) -> HandlerResult:
        ctx.conn.execute(domain.insert().values(domain_id="0000", name="Indonesia", level="pusat"))
        return HandlerResult()

    _seed(db_engine, "write", {})
    await _run(db_engine, {"write": write})
    with db_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(domain)).scalar_one() == 1


async def test_raw_sha256_is_independent_of_key_order(db_engine: Engine) -> None:
    async def h(ctx: TaskContext) -> HandlerResult:
        body = {"b": 1, "a": [1, 2]} if ctx.params["i"] == 0 else {"a": [1, 2], "b": 1}
        return HandlerResult(raw=[RawResponse("x", {}, body)])

    _seed(db_engine, "h", {"i": 0}, {"i": 1})
    await _run(db_engine, {"h": h})
    a, b = _raw(db_engine)
    assert a.sha256 == b.sha256 == _sha({"a": [1, 2], "b": 1})


# --- failures ------------------------------------------------------------------------------------


async def test_handler_exception_fails_task_and_persists_nothing(db_engine: Engine) -> None:
    async def boom(ctx: TaskContext) -> HandlerResult:
        ctx.conn.execute(domain.insert().values(domain_id="0000", name="X", level="pusat"))
        raise RuntimeError("handler blew up")

    (task_id,) = _seed(db_engine, "boom", {})
    stats = await _run(db_engine, {"boom": boom})

    assert stats.failed == 1
    assert stats.done == 0
    row = _tasks(db_engine)[task_id]
    assert row.status == queue.PENDING
    assert row.attempts == 1
    assert "handler blew up" in row.last_error
    assert "RuntimeError" in row.last_error
    assert _raw(db_engine) == []
    with db_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(domain)).scalar_one() == 0


async def test_failure_after_result_rolls_back_raw_and_children(db_engine: Engine) -> None:
    """Storing the result fails (a raw body that isn't JSON): raw rows + children are dropped."""

    async def bad_result(ctx: TaskContext) -> HandlerResult:
        return HandlerResult(
            raw=[RawResponse("ok", {}, {"fine": True}), RawResponse("bad", {}, {"x": object()})],
            children=[Child("child", {"n": 1})],
        )

    (task_id,) = _seed(db_engine, "bad", {})
    stats = await _run(db_engine, {"bad": bad_result})
    assert stats.failed == 1
    rows = _tasks(db_engine)
    assert list(rows) == [task_id]
    assert rows[task_id].attempts == 1
    assert _raw(db_engine) == []


async def test_unknown_kind_fails(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, "mystery", {})
    stats = await _run(db_engine, {})
    assert stats.failed == 1
    row = _tasks(db_engine)[task_id]
    assert row.attempts == 1
    assert "mystery" in row.last_error


async def test_api_key_never_stored(db_engine: Engine) -> None:
    async def leaky(ctx: TaskContext) -> HandlerResult:
        return HandlerResult(
            raw=[
                RawResponse(
                    f"list?model=var&key={SECRET}",
                    {"model": "var", "key": SECRET, "note": f"https://x/key/{SECRET}/"},
                    {"ok": True},
                )
            ]
        )

    async def leaky_error(ctx: TaskContext) -> HandlerResult:
        raise RuntimeError(f"bad thing with {SECRET} inside")

    _seed(db_engine, "leaky", {})
    (err_id,) = _seed(db_engine, "leaky_error", {})
    await _run(db_engine, {"leaky": leaky, "leaky_error": leaky_error}, secrets=[SECRET])

    (raw,) = _raw(db_engine)
    assert SECRET not in raw.endpoint
    assert SECRET not in json.dumps(raw.params)
    assert "key" not in raw.params
    assert raw.params["model"] == "var"
    assert SECRET not in _tasks(db_engine)[err_id].last_error


async def test_cancelled_worker_leaves_task_pending(db_engine: Engine) -> None:
    """Crash/cancel mid-handler: the transaction rolls back, the task is claimable again."""
    started = asyncio.Event()

    async def hang(ctx: TaskContext) -> HandlerResult:
        started.set()
        await asyncio.sleep(60)
        return HandlerResult()

    (task_id,) = _seed(db_engine, "hang", {})
    run = asyncio.create_task(_run(db_engine, {"hang": hang}))
    await asyncio.wait_for(started.wait(), 5)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    row = _tasks(db_engine)[task_id]
    assert row.status == queue.PENDING
    assert row.attempts == 0


# --- stopping + concurrency ----------------------------------------------------------------------


async def test_drain_runs_children_until_queue_empty(db_engine: Engine) -> None:
    async def tree(ctx: TaskContext) -> HandlerResult:
        depth = ctx.params["depth"]
        if depth == 2:
            return HandlerResult()
        return HandlerResult(
            children=[
                Child("tree", {"depth": depth + 1, "i": f"{ctx.params['i']}.{j}"}) for j in range(2)
            ]
        )

    _seed(db_engine, "tree", {"depth": 0, "i": "0"})
    stats = await _run(db_engine, {"tree": tree}, concurrency=3, drain=True)
    rows = _tasks(db_engine)
    assert len(rows) == 7  # 1 + 2 + 4
    assert {r.status for r in rows.values()} == {queue.DONE}
    assert stats.done == 7


async def test_stops_after_max_tasks(db_engine: Engine) -> None:
    calls: list[int] = []

    async def h(ctx: TaskContext) -> HandlerResult:
        calls.append(ctx.params["i"])
        return HandlerResult()

    _seed(db_engine, "h", *({"i": i} for i in range(5)))
    stats = await _run(db_engine, {"h": h}, concurrency=3, max_tasks=2, drain=False)
    assert stats.done == 2
    assert len(calls) == 2
    statuses = [r.status for r in _tasks(db_engine).values()]
    assert statuses.count(queue.DONE) == 2
    assert statuses.count(queue.PENDING) == 3


async def test_concurrency_runs_tasks_in_parallel_each_once(db_engine: Engine) -> None:
    active = 0
    peak = 0
    handled: list[int] = []
    conns: set[int] = set()

    async def slow(ctx: TaskContext) -> HandlerResult:
        nonlocal active, peak
        conns.add(id(ctx.conn))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        handled.append(ctx.params["i"])
        return HandlerResult()

    _seed(db_engine, "slow", *({"i": i} for i in range(9)))
    stats = await _run(db_engine, {"slow": slow}, concurrency=3)
    assert stats.done == 9
    assert sorted(handled) == list(range(9))
    assert peak == 3
    assert len(conns) >= 3  # one connection per slot


async def test_drain_on_empty_queue_returns_immediately(db_engine: Engine) -> None:
    stats = await asyncio.wait_for(_run(db_engine, {}, concurrency=2), 5)
    assert (stats.done, stats.failed) == (0, 0)


def test_params_redacted_drops_key_param() -> None:
    assert worker.clean_params({"key": SECRET, "a": f"x?key={SECRET}"}, [SECRET]) == {
        "a": "x?key=***"
    }


def test_handler_registry_is_a_dict() -> None:
    assert isinstance(worker.HANDLERS, dict)


def test_parse_args() -> None:
    args = worker.parse_args(["--drain", "--max-tasks", "3", "--kind", "a", "--kind", "b"])
    assert args.drain is True
    assert args.max_tasks == 3
    assert args.kinds == ["a", "b"]
    assert args.concurrency is None
