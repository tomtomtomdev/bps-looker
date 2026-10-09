"""Worker runner: claim tasks from the queue and run their handlers, ``concurrency`` at a time.

Handler contract — ``async def handler(ctx: TaskContext) -> HandlerResult``:

- ``ctx.params`` are the task params, ``ctx.client`` the API client (anything with
  ``async get(path, **params) -> dict``), ``ctx.conn`` the slot's DB connection. Handlers may
  write their parsed rows through ``ctx.conn``; those writes share the task's transaction.
- The returned :class:`HandlerResult` lists raw responses to store in ``raw_response``
  (``sha256`` = hash of the canonical body JSON) and child tasks to enqueue.

Each task runs in **one transaction on the slot's own connection**::

    claim(1) → savepoint[ handler → store raw responses → enqueue children ] → complete
                                         └─ any exception → savepoint rolled back → fail

So a failed attempt persists nothing but ``attempts``/``last_error``/backoff, and a crash
(or cancellation) rolls the whole transaction back, leaving the task ``pending``. The
transaction stays open across the handler's HTTP calls — the row lock is what keeps the task
claimed — so handlers should do one task's worth of fetching and no more.

DB calls are synchronous (SQLAlchemy Core) and briefly block the event loop; they are short
next to the rate-limited HTTP calls the slots overlap on.

The API key never reaches the DB: stored endpoints/params and error text are redacted, and a
``key`` param is dropped.
"""

import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import Connection, Engine, create_engine, insert

from bps_fetcher import queue
from bps_fetcher.db.schema import raw_response
from bps_fetcher.recorder import scrub
from bps_fetcher.redact import install_redaction, redact

log = logging.getLogger(__name__)

DEFAULT_IDLE_SLEEP = 5.0
# ``bps work --drain`` waits up to this long for a pending task that is only waiting out its
# retry backoff (the longest backoff before a task goes dead is 30 s * 2**3 = 4 min).
DEFAULT_DRAIN_WAIT = 300.0


class ApiClient(Protocol):
    async def get(self, path: str, **params: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RawResponse:
    """One API response to keep: ``endpoint`` (path), request ``params`` (no key), ``body``."""

    endpoint: str
    params: Mapping[str, Any]
    body: Any


@dataclass(frozen=True, slots=True)
class Child:
    """A task to enqueue (idempotent on ``(kind, params)``) with the current task as parent."""

    kind: str
    params: Mapping[str, Any]


@dataclass(slots=True)
class HandlerResult:
    raw: list[RawResponse] = field(default_factory=list)
    children: list[Child] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TaskContext:
    task: queue.Task
    client: ApiClient
    conn: Connection

    @property
    def params(self) -> dict[str, Any]:
        return self.task.params


Handler = Callable[[TaskContext], Awaitable[HandlerResult]]

# kind -> handler, filled by ``@register`` in ``bps_fetcher.handlers`` (imported at the bottom).
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    """Decorator: add a handler to :data:`HANDLERS` under ``kind``."""

    def deco(fn: Handler) -> Handler:
        if kind in HANDLERS and HANDLERS[kind] is not fn:
            raise ValueError(f"handler for {kind!r} already registered")
        HANDLERS[kind] = fn
        return fn

    return deco


@dataclass(frozen=True, slots=True)
class Refresher:
    """Run ``fn(engine)`` (e.g. a materialized view refresh) after tasks of ``kinds`` completed:
    during a run at most every ``interval`` seconds (in a thread, so the other slots keep going),
    and once more when the worker stops if anything completed since. Errors are logged, never
    raised — the next completed task retries."""

    kinds: frozenset[str]
    fn: Callable[[Engine], object]
    interval: float = 600.0


class _RefreshState:
    def __init__(self, refresher: Refresher) -> None:
        self.refresher = refresher
        self.dirty = False
        self.running = False
        self.last = time.monotonic()

    async def run(self, engine: Engine, *, force: bool = False) -> None:
        if not self.dirty or self.running:
            return
        if not force and time.monotonic() - self.last < self.refresher.interval:
            return
        self.dirty = False
        self.running = True
        try:
            await asyncio.to_thread(self.refresher.fn, engine)
        except Exception as exc:
            self.dirty = True
            log.warning("refresh after %s tasks failed: %s", sorted(self.refresher.kinds), exc)
        finally:
            self.running = False
            self.last = time.monotonic()


@dataclass(slots=True)
class Stats:
    done: int = 0
    failed: int = 0


class UnknownTaskKindError(LookupError):
    pass


def clean_params(params: Mapping[str, Any], secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Drop any ``key`` param and redact the key from every string value."""
    secrets = list(secrets)
    return {
        redact(str(k), secrets): scrub(v, secrets)
        for k, v in params.items()
        if str(k).lower() != "key"
    }


def store_raw(
    conn: Connection, task_id: int, responses: Sequence[RawResponse], secrets: Iterable[str] = ()
) -> None:
    secrets = list(secrets)
    for r in responses:
        conn.execute(
            insert(raw_response).values(
                task_id=task_id,
                endpoint=redact(r.endpoint, secrets),
                params=clean_params(r.params, secrets),
                body=r.body,
                sha256=queue.canonical_sha256(r.body),
            )
        )


async def run_one(
    engine: Engine,
    client: ApiClient,
    handlers: Mapping[str, Handler],
    *,
    secrets: Sequence[str] = (),
    kinds: Iterable[str] | None = None,
    on_complete: Callable[[queue.Task], object] | None = None,
) -> bool | None:
    """Claim and process one task in its own transaction.

    Returns ``None`` if nothing was due, ``True`` if the task completed, ``False`` if it failed.
    ``on_complete(task)`` is called once a completed task's transaction committed.
    """
    with engine.connect() as conn, conn.begin():
        claimed = queue.claim(conn, 1, kinds=kinds)
        if not claimed:
            return None
        (t,) = claimed
        try:
            with conn.begin_nested():
                handler = handlers.get(t.kind)
                if handler is None:
                    raise UnknownTaskKindError(f"no handler for task kind {t.kind!r}")
                result = await handler(TaskContext(t, client, conn))
                store_raw(conn, t.id, result.raw, secrets)
                for child in result.children:
                    queue.enqueue(conn, child.kind, child.params, parent_id=t.id)
        except Exception as exc:
            error = redact(f"{type(exc).__name__}: {exc}", secrets)
            status = queue.fail(conn, t.id, error)
            log.warning("task %s (%s) failed -> %s: %s", t.id, t.kind, status, error)
            return False
        queue.complete(conn, t.id)
    log.debug("task %s (%s) done", t.id, t.kind)
    if on_complete is not None:
        on_complete(t)
    return True


def _seconds_until_due(engine: Engine, kinds: list[str] | None) -> float | None:
    with engine.connect() as conn:
        return queue.seconds_until_due(conn, kinds=kinds)


async def run_worker(
    engine: Engine,
    *,
    client: ApiClient,
    handlers: Mapping[str, Handler] | None = None,
    concurrency: int = 1,
    drain: bool = True,
    max_tasks: int | None = None,
    kinds: Iterable[str] | None = None,
    secrets: Sequence[str] = (),
    idle_sleep: float = DEFAULT_IDLE_SLEEP,
    drain_wait: float = 0.0,
    refreshers: Sequence[Refresher] = (),
) -> Stats:
    """Run ``concurrency`` slots, each claiming one task at a time on its own connection.

    Stops after ``max_tasks`` claimed tasks, or — with ``drain`` — once no task is due, no
    slot is still working (children of in-flight tasks count as future work) and no pending
    task becomes due within ``drain_wait`` seconds (e.g. one waiting out its retry backoff;
    the default ``0`` stops right away). Without either it polls forever, sleeping
    ``idle_sleep`` when the queue is empty. ``refreshers`` run after tasks of their kinds
    completed (see :class:`Refresher`; a cancelled worker skips the final refresh).
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    handlers = HANDLERS if handlers is None else handlers
    kind_list = list(kinds) if kinds is not None else None
    stats = Stats()
    claimed = 0
    busy = 0
    refresh_states = [_RefreshState(r) for r in refreshers]

    def mark_dirty(t: queue.Task) -> None:
        for state in refresh_states:
            if t.kind in state.refresher.kinds:
                state.dirty = True

    async def slot() -> None:
        nonlocal claimed, busy
        while max_tasks is None or claimed < max_tasks:
            claimed += 1  # reserve before awaiting so slots never overshoot max_tasks
            busy += 1
            try:
                outcome = await run_one(
                    engine,
                    client,
                    handlers,
                    secrets=secrets,
                    kinds=kind_list,
                    on_complete=mark_dirty if refresh_states else None,
                )
            finally:
                busy -= 1
            if outcome is True:
                stats.done += 1
                for state in refresh_states:
                    await state.run(engine)
            elif outcome is False:
                stats.failed += 1
            else:
                claimed -= 1
                sleep = idle_sleep
                if drain and busy == 0:
                    wait = _seconds_until_due(engine, kind_list)
                    if wait is None or wait > drain_wait:
                        return
                    sleep = min(idle_sleep, max(wait, 0.01))
                await asyncio.sleep(sleep)

    async with asyncio.TaskGroup() as tg:
        for _ in range(concurrency):
            tg.create_task(slot())
    for state in refresh_states:
        await state.run(engine, force=True)
    return stats


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bps_fetcher.worker", description=__doc__)
    p.add_argument("--drain", action="store_true", help="stop when no task is due")
    p.add_argument("--max-tasks", type=int, default=None, help="stop after N tasks")
    p.add_argument("--concurrency", type=int, default=None, help="default: BPS_CONCURRENCY")
    p.add_argument("--kind", action="append", dest="kinds", help="only these task kinds")
    p.add_argument(
        "--drain-wait",
        type=float,
        default=DEFAULT_DRAIN_WAIT,
        help="with --drain: wait up to N seconds for tasks in retry backoff (default %(default)s)",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    from bps_fetcher.client import BpsClient
    from bps_fetcher.settings import get_settings

    args = parse_args(argv)
    settings = get_settings()
    key = settings.api_key.get_secret_value()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_redaction([key])
    engine = create_engine(settings.database_url)

    async def _go() -> Stats:
        async with BpsClient.from_settings(settings) as client:
            return await run_worker(
                engine,
                client=client,
                concurrency=args.concurrency or settings.concurrency,
                drain=args.drain,
                max_tasks=args.max_tasks,
                kinds=args.kinds,
                secrets=[key],
                drain_wait=args.drain_wait,
            )

    try:
        stats = asyncio.run(_go())
    finally:
        engine.dispose()
    log.info("worker stopped: %d done, %d failed", stats.done, stats.failed)
    return 0


# Register the real handlers (after HANDLERS/register exist: handlers import this module).
from bps_fetcher import handlers as _handlers  # noqa: E402, F401

if __name__ == "__main__":  # pragma: no cover
    # Under ``python -m`` this file is ``__main__``, but handlers register into the importable
    # ``bps_fetcher.worker`` module — run that module's main so its HANDLERS are used.
    from bps_fetcher.worker import main as _main

    raise SystemExit(_main())
