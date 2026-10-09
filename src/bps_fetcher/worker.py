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

# kind -> handler. Filled by later slices (domains, var_list, th_list, data, ...).
HANDLERS: dict[str, Handler] = {}


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
) -> bool | None:
    """Claim and process one task in its own transaction.

    Returns ``None`` if nothing was due, ``True`` if the task completed, ``False`` if it failed.
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
        return True


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
) -> Stats:
    """Run ``concurrency`` slots, each claiming one task at a time on its own connection.

    Stops after ``max_tasks`` claimed tasks, or — with ``drain`` — once no task is due and no
    slot is still working (children of in-flight tasks count as future work). Without either
    it polls forever, sleeping ``idle_sleep`` when the queue is empty.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    handlers = HANDLERS if handlers is None else handlers
    kind_list = list(kinds) if kinds is not None else None
    stats = Stats()
    claimed = 0
    busy = 0

    async def slot() -> None:
        nonlocal claimed, busy
        while max_tasks is None or claimed < max_tasks:
            claimed += 1  # reserve before awaiting so slots never overshoot max_tasks
            busy += 1
            try:
                outcome = await run_one(engine, client, handlers, secrets=secrets, kinds=kind_list)
            finally:
                busy -= 1
            if outcome is True:
                stats.done += 1
            elif outcome is False:
                stats.failed += 1
            else:
                claimed -= 1
                if drain and busy == 0:
                    return
                await asyncio.sleep(idle_sleep)

    async with asyncio.TaskGroup() as tg:
        for _ in range(concurrency):
            tg.create_task(slot())
    return stats


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bps_fetcher.worker", description=__doc__)
    p.add_argument("--drain", action="store_true", help="stop when no task is due")
    p.add_argument("--max-tasks", type=int, default=None, help="stop after N tasks")
    p.add_argument("--concurrency", type=int, default=None, help="default: BPS_CONCURRENCY")
    p.add_argument("--kind", action="append", dest="kinds", help="only these task kinds")
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
            )

    try:
        stats = asyncio.run(_go())
    finally:
        engine.dispose()
    log.info("worker stopped: %d done, %d failed", stats.done, stats.failed)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
