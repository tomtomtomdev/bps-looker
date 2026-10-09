"""Postgres task queue on the ``task`` table (SQLAlchemy 2 Core, ``FOR UPDATE SKIP LOCKED``).

Statuses (``task.status``, enforced by ``ck_task_status``):

- ``pending`` — waiting to run once ``next_run_at <= now()``.
- ``running`` — claimed by a worker.
- ``done``    — handler succeeded.
- ``dead``    — failed ``max_attempts`` times; left for inspection (``last_error``).

Every function takes a :class:`~sqlalchemy.Connection` and runs inside the caller's
transaction, so a worker can do *claim → handler → store raw response → enqueue children →
complete* atomically::

    with engine.connect() as conn, conn.begin():
        for t in claim(conn, 1):
            try:
                with conn.begin_nested():   # savepoint: handler writes
                    ...                      # fetch, store raw_response, enqueue children
                complete(conn, t.id)
            except Exception as exc:
                fail(conn, t.id, str(exc))   # savepoint rolled back; row lock still held

Crash recovery: while the claiming transaction is open, the row stays locked and the
``running`` status is uncommitted — if the worker dies, Postgres rolls back and the task is
``pending`` again with no extra work. Only a claim that was *committed* separately can be
orphaned in ``running``; :func:`requeue_stale` resets those after a timeout.

No sync call here may wait on another worker slot (slots share one event loop, so waiting on a
lock another slot holds deadlocks the process — seen in the S18 refresh run): row locks are
``FOR NO KEY UPDATE`` (so a child's ``parent_id`` FK check, ``FOR KEY SHARE`` on the parent,
doesn't wait on a claimed parent), re-pending skips locked rows, and :func:`enqueue` looks for an
existing row before inserting.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import Connection, extract, func, select, update
from sqlalchemy.dialects.postgresql import insert

from bps_fetcher.db.schema import task
from bps_fetcher.redact import redact

PENDING: Final = "pending"
RUNNING: Final = "running"
DONE: Final = "done"
DEAD: Final = "dead"

# :func:`schedule` outcomes.
NEW: Final = "new"
RERUN: Final = "rerun"

DEFAULT_MAX_ATTEMPTS: Final = 5
DEFAULT_BASE_DELAY: Final = timedelta(seconds=30)
DEFAULT_MAX_DELAY: Final = timedelta(hours=6)
MAX_ERROR_LEN: Final = 2000


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    kind: str
    params: dict[str, Any]
    attempts: int
    parent_id: int | None


def canonical_sha256(value: Any) -> str:
    """SHA-256 hex of canonical JSON (sorted keys, compact separators, UTF-8)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def params_hash(params: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of ``params`` (see :func:`canonical_sha256`)."""
    return canonical_sha256(params)


def backoff(
    attempts: int, *, base: timedelta = DEFAULT_BASE_DELAY, cap: timedelta = DEFAULT_MAX_DELAY
) -> timedelta:
    """Delay before retry number ``attempts`` (1-based): ``base * 2**(attempts-1)``, capped."""
    exponent = min(max(attempts - 1, 0), 32)  # avoid huge ints; cap applies long before
    delay: timedelta = base * (2**exponent)
    return min(delay, cap)


def enqueue(
    conn: Connection,
    kind: str,
    params: Mapping[str, Any],
    *,
    parent_id: int | None = None,
) -> int | None:
    """Insert a pending task; returns its id, or ``None`` if ``(kind, params)`` already exists.

    An existing (committed) task is found with a plain ``SELECT`` first: ``ON CONFLICT DO
    NOTHING`` alone would wait while another transaction updates that row (e.g. a worker slot
    that claimed it), and slots share one event loop — a sync wait there deadlocks the process.
    """
    digest = params_hash(params)
    existing = select(task.c.id).where(task.c.kind == kind, task.c.params_hash == digest)
    if conn.execute(existing).first() is not None:
        return None
    stmt = (
        insert(task)
        .values(
            kind=kind,
            params=dict(params),
            params_hash=digest,
            parent_id=parent_id,
        )
        .on_conflict_do_nothing(index_elements=[task.c.kind, task.c.params_hash])
        .returning(task.c.id)
    )
    return conn.execute(stmt).scalar_one_or_none()


def claim(conn: Connection, n: int, *, kinds: Iterable[str] | None = None) -> list[Task]:
    """Lock and mark ``running`` up to ``n`` due pending tasks (oldest due first).

    Rows locked by other transactions are skipped, so concurrent claimers never share a task.
    """
    due = (
        select(task.c.id)
        .where(task.c.status == PENDING, task.c.next_run_at <= func.now())
        .order_by(task.c.next_run_at, task.c.id)
        .limit(n)
        .with_for_update(skip_locked=True, key_share=True)
    )
    if kinds is not None:
        due = due.where(task.c.kind.in_(list(kinds)))
    ids = list(conn.execute(due).scalars())
    if not ids:
        return []
    rows = conn.execute(
        update(task)
        .where(task.c.id.in_(ids))
        .values(status=RUNNING, updated_at=func.now())
        .returning(task.c.id, task.c.kind, task.c.params, task.c.attempts, task.c.parent_id)
    ).all()
    by_id = {r.id: Task(r.id, r.kind, r.params, r.attempts, r.parent_id) for r in rows}
    return [by_id[i] for i in ids]


def complete(conn: Connection, task_id: int) -> None:
    conn.execute(
        update(task)
        .where(task.c.id == task_id)
        .values(status=DONE, last_error=None, updated_at=func.now())
    )


def fail(
    conn: Connection,
    task_id: int,
    error: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: timedelta = DEFAULT_BASE_DELAY,
    max_delay: timedelta = DEFAULT_MAX_DELAY,
) -> str:
    """Record a failed attempt; reschedule with backoff, or mark ``dead`` at ``max_attempts``.

    Returns the new status. ``error`` is redacted and truncated to :data:`MAX_ERROR_LEN`.
    """
    attempts = conn.execute(
        select(task.c.attempts).where(task.c.id == task_id).with_for_update(key_share=True)
    ).scalar_one()
    attempts += 1
    status = DEAD if attempts >= max_attempts else PENDING
    values: dict[str, Any] = {
        "attempts": attempts,
        "status": status,
        "last_error": redact(error)[:MAX_ERROR_LEN],
        "updated_at": func.now(),
    }
    if status == PENDING:
        values["next_run_at"] = func.now() + backoff(attempts, base=base_delay, cap=max_delay)
    conn.execute(update(task).where(task.c.id == task_id).values(**values))
    return status


def requeue_stale(conn: Connection, *, older_than: timedelta) -> int:
    """Reset ``running`` tasks untouched for ``older_than`` back to ``pending``; returns count."""
    stale = (
        select(task.c.id)
        .where(task.c.status == RUNNING, task.c.updated_at < func.now() - older_than)
        .with_for_update(skip_locked=True, key_share=True)
    )
    result = conn.execute(
        update(task)
        .where(task.c.id.in_(stale.scalar_subquery()))
        .values(status=PENDING, updated_at=func.now())
        .returning(task.c.id)
    )
    return len(result.all())


def rerun_done(
    conn: Connection, ids: Iterable[int], *, done_before: datetime | None = None
) -> list[int]:
    """Make ``done`` tasks runnable again: ``pending``, due now, attempts/last_error reset.

    Only tasks still ``done`` (and, with ``done_before``, completed at or before it — a task's
    ``updated_at`` is its completion time) are touched; returns the ids that were re-pended.
    Rows locked by another transaction (a claimed/running task, or one another transaction is
    re-pending) are skipped, never waited on: worker slots share one event loop, and a blocking
    UPDATE there would deadlock the process (seen in the S18 refresh run).
    """
    ids = list(ids)
    if not ids:
        return []
    due = (
        select(task.c.id)
        .where(task.c.id.in_(ids), task.c.status == DONE)
        .with_for_update(skip_locked=True, key_share=True)
    )
    if done_before is not None:
        due = due.where(task.c.updated_at <= done_before)
    rows = conn.execute(
        update(task)
        .where(task.c.id.in_(due.scalar_subquery()))
        .values(
            status=PENDING,
            attempts=0,
            last_error=None,
            next_run_at=func.now(),
            updated_at=func.now(),
        )
        .returning(task.c.id)
    )
    return list(rows.scalars())


def schedule(
    conn: Connection,
    kind: str,
    params: Mapping[str, Any],
    *,
    done_before: datetime | None = None,
    parent_id: int | None = None,
) -> str | None:
    """Make sure ``(kind, params)`` will run: enqueue it (:data:`NEW`), or re-pend it if it is
    ``done`` (and completed at or before ``done_before``) (:data:`RERUN`). Pending, running and
    dead tasks are left alone (``None``), so calling this twice in a row is a no-op."""
    tid = conn.execute(
        select(task.c.id).where(task.c.kind == kind, task.c.params_hash == params_hash(params))
    ).scalar_one_or_none()
    if tid is None:
        return NEW if enqueue(conn, kind, params, parent_id=parent_id) is not None else None
    return RERUN if rerun_done(conn, [tid], done_before=done_before) else None


def seconds_until_due(conn: Connection, *, kinds: Iterable[str] | None = None) -> float | None:
    """Seconds until the earliest ``pending`` task is due (0 if one is due now); ``None`` if no
    task is pending."""
    q = select(extract("epoch", func.min(task.c.next_run_at) - func.now())).where(
        task.c.status == PENDING
    )
    if kinds is not None:
        q = q.where(task.c.kind.in_(list(kinds)))
    wait = conn.execute(q).scalar_one()
    return None if wait is None else max(float(wait), 0.0)
