"""S6: Postgres task queue (DB tests skipped when Postgres is unreachable)."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from bps_fetcher import queue
from bps_fetcher.db.schema import task

# --- pure helpers (no DB) ------------------------------------------------------------------------


def test_params_hash_is_stable_and_order_independent() -> None:
    a = queue.params_hash({"domain": "0000", "var": 1804, "th": [124, 125]})
    b = queue.params_hash({"th": [124, 125], "var": 1804, "domain": "0000"})
    assert a == b
    assert len(a) == 64
    assert a == queue.params_hash({"var": 1804, "domain": "0000", "th": [124, 125]})


def test_params_hash_differs_for_different_params() -> None:
    assert queue.params_hash({"var": 1}) != queue.params_hash({"var": 2})
    assert queue.params_hash({"var": 1}) != queue.params_hash({"var": "1"})
    assert queue.params_hash({}) != queue.params_hash({"x": None})


def test_backoff_grows_exponentially_and_is_capped() -> None:
    base, cap = timedelta(seconds=30), timedelta(hours=1)
    assert queue.backoff(1, base=base, cap=cap) == timedelta(seconds=30)
    assert queue.backoff(2, base=base, cap=cap) == timedelta(seconds=60)
    assert queue.backoff(3, base=base, cap=cap) == timedelta(seconds=120)
    assert queue.backoff(20, base=base, cap=cap) == cap


# --- enqueue -------------------------------------------------------------------------------------


def test_enqueue_inserts_pending_task(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        task_id = queue.enqueue(conn, "var_list", {"domain": "0000"})
        row = conn.execute(select(task).where(task.c.id == task_id)).one()
    assert row.kind == "var_list"
    assert row.params == {"domain": "0000"}
    assert row.params_hash == queue.params_hash({"domain": "0000"})
    assert row.status == queue.PENDING
    assert row.attempts == 0


def test_enqueue_is_idempotent_on_kind_and_params(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        first = queue.enqueue(conn, "var_list", {"domain": "0000", "page": 1})
        again = queue.enqueue(conn, "var_list", {"page": 1, "domain": "0000"})
        other_kind = queue.enqueue(conn, "th_list", {"domain": "0000", "page": 1})
        count = conn.execute(select(func.count()).select_from(task)).scalar_one()
    assert first is not None
    assert again is None
    assert other_kind is not None
    assert count == 2


def test_enqueue_records_parent(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        parent = queue.enqueue(conn, "domains", {})
        child = queue.enqueue(conn, "var_list", {"domain": "1100"}, parent_id=parent)
        row = conn.execute(select(task.c.parent_id).where(task.c.id == child)).one()
    assert row.parent_id == parent


def test_status_check_constraint_rejects_unknown_status(db_engine: Engine) -> None:
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(insert(task).values(kind="k", params_hash="h", status="bogus"))


# --- claim ---------------------------------------------------------------------------------------


def _seed(engine: Engine, n: int, kind: str = "k") -> list[int]:
    with engine.begin() as conn:
        ids = [queue.enqueue(conn, kind, {"i": i}) for i in range(n)]
    return [i for i in ids if i is not None]


def test_claim_returns_up_to_n_and_marks_running(db_engine: Engine) -> None:
    ids = _seed(db_engine, 3)
    with db_engine.begin() as conn:
        claimed = queue.claim(conn, 2)
    assert [t.id for t in claimed] == ids[:2]
    assert claimed[0].kind == "k"
    assert claimed[0].params == {"i": 0}
    with db_engine.connect() as conn:
        statuses = dict(conn.execute(select(task.c.id, task.c.status)).all())
    assert statuses == {ids[0]: queue.RUNNING, ids[1]: queue.RUNNING, ids[2]: queue.PENDING}


def test_claim_skips_future_and_non_pending_tasks(db_engine: Engine) -> None:
    ids = _seed(db_engine, 3)
    with db_engine.begin() as conn:
        conn.execute(
            update(task)
            .where(task.c.id == ids[0])
            .values(next_run_at=func.now() + timedelta(hours=1))
        )
        queue.complete(conn, ids[1])
    with db_engine.begin() as conn:
        claimed = queue.claim(conn, 10)
    assert [t.id for t in claimed] == [ids[2]]


def test_claim_can_filter_by_kind(db_engine: Engine) -> None:
    _seed(db_engine, 2, kind="a")
    b_ids = _seed(db_engine, 1, kind="b")
    with db_engine.begin() as conn:
        claimed = queue.claim(conn, 10, kinds=["b"])
    assert [t.id for t in claimed] == b_ids


def test_claim_on_empty_queue_returns_nothing(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        assert queue.claim(conn, 5) == []


def test_concurrent_claimers_never_get_the_same_task(db_engine: Engine) -> None:
    """Two open transactions: the second skips rows the first has locked (no blocking)."""
    ids = _seed(db_engine, 3)
    with db_engine.connect() as c1, db_engine.connect() as c2:
        t1 = c1.begin()
        t2 = c2.begin()
        # Without SKIP LOCKED the second claim would block; fail fast instead of hanging.
        c2.execute(text("SET LOCAL lock_timeout = '2s'"))
        first = queue.claim(c1, 2)
        second = queue.claim(c2, 2)
        third = queue.claim(c2, 2)
        t1.commit()
        t2.commit()
    got1 = {t.id for t in first}
    got2 = {t.id for t in second}
    assert got1 == set(ids[:2])
    assert got2 == {ids[2]}
    assert third == []


def test_crashed_claimer_rollback_returns_task_to_queue(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, 1)
    with db_engine.connect() as conn:
        tx = conn.begin()
        assert [t.id for t in queue.claim(conn, 1)] == [task_id]
        tx.rollback()  # worker died mid-transaction
    with db_engine.begin() as conn:
        assert [t.id for t in queue.claim(conn, 1)] == [task_id]


# --- complete / fail -----------------------------------------------------------------------------


def test_complete_marks_done(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, 1)
    with db_engine.begin() as conn:
        queue.claim(conn, 1)
        queue.complete(conn, task_id)
        row = conn.execute(select(task).where(task.c.id == task_id)).one()
    assert row.status == queue.DONE
    assert row.last_error is None


def test_fail_increments_attempts_and_backs_off(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, 1)
    base = timedelta(seconds=30)
    with db_engine.begin() as conn:
        queue.claim(conn, 1)
        status = queue.fail(conn, task_id, "boom", max_attempts=5, base_delay=base)
        row = conn.execute(
            select(task, (task.c.next_run_at - func.now()).label("delay")).where(
                task.c.id == task_id
            )
        ).one()
    assert status == queue.PENDING
    assert row.status == queue.PENDING
    assert row.attempts == 1
    assert row.last_error == "boom"
    assert row.delay == base
    with db_engine.begin() as conn:
        assert queue.claim(conn, 1) == []  # not due yet

    with db_engine.begin() as conn:
        queue.fail(conn, task_id, "boom again", max_attempts=5, base_delay=base)
        row = conn.execute(
            select(task, (task.c.next_run_at - func.now()).label("delay")).where(
                task.c.id == task_id
            )
        ).one()
    assert row.attempts == 2
    assert row.delay == 2 * base


def test_fail_redacts_and_truncates_error(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, 1)
    with db_engine.begin() as conn:
        queue.fail(conn, task_id, "GET /v1/api/list?key=SECRETKEY123 " + "x" * 10_000)
        row = conn.execute(select(task.c.last_error).where(task.c.id == task_id)).one()
    assert "SECRETKEY123" not in row.last_error
    assert len(row.last_error) <= queue.MAX_ERROR_LEN


def test_task_over_max_attempts_is_dead(db_engine: Engine) -> None:
    (task_id,) = _seed(db_engine, 1)
    statuses = []
    for _ in range(3):
        with db_engine.begin() as conn:
            statuses.append(queue.fail(conn, task_id, "err", max_attempts=3))
    assert statuses == [queue.PENDING, queue.PENDING, queue.DEAD]
    with db_engine.begin() as conn:
        conn.execute(update(task).values(next_run_at=func.now() - timedelta(days=1)))
        row = conn.execute(select(task).where(task.c.id == task_id)).one()
        assert queue.claim(conn, 1) == []
    assert row.status == queue.DEAD
    assert row.attempts == 3


# --- recovery ------------------------------------------------------------------------------------


def test_requeue_stale_running_resets_committed_orphans(db_engine: Engine) -> None:
    """A claim committed by a worker that then died leaves a 'running' row; recover it."""
    stale, fresh = _seed(db_engine, 2)
    with db_engine.begin() as conn:
        queue.claim(conn, 2)
        conn.execute(
            update(task)
            .where(task.c.id == stale)
            .values(updated_at=func.now() - timedelta(hours=2))
        )
    with db_engine.begin() as conn:
        n = queue.requeue_stale(conn, older_than=timedelta(hours=1))
        statuses = dict(conn.execute(select(task.c.id, task.c.status)).all())
    assert n == 1
    assert statuses == {stale: queue.PENDING, fresh: queue.RUNNING}


def test_single_transaction_flow_with_savepoint(db_engine: Engine) -> None:
    """S7 shape: claim, handler work in a savepoint, enqueue children, complete — or fail."""
    ok_id, bad_id = _seed(db_engine, 2)
    with db_engine.connect() as conn, conn.begin():
        for t in queue.claim(conn, 2):
            try:
                with conn.begin_nested():
                    queue.enqueue(conn, "child", {"of": t.id}, parent_id=t.id)
                    if t.id == bad_id:
                        raise RuntimeError("handler blew up")
                queue.complete(conn, t.id)
            except RuntimeError as exc:
                queue.fail(conn, t.id, str(exc))
    with db_engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(select(task)).all()}
    assert rows[ok_id].status == queue.DONE
    assert rows[bad_id].status == queue.PENDING
    assert rows[bad_id].attempts == 1
    children = [r for r in rows.values() if r.kind == "child"]
    assert [c.parent_id for c in children] == [ok_id]  # failed handler's child rolled back


# --- re-running done tasks (S18) -----------------------------------------------------------------


def _mark(engine: Engine, tid: int, status: str, updated_at: Any = None) -> None:
    values: dict[str, Any] = {"status": status, "attempts": 3, "last_error": "old"}
    if updated_at is not None:
        values["updated_at"] = updated_at
    with engine.begin() as conn:
        conn.execute(update(task).where(task.c.id == tid).values(**values))


def test_schedule_enqueues_new_or_reruns_done(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        assert queue.schedule(conn, "k", {"a": 1}) == queue.NEW
        tid = conn.execute(select(task.c.id)).scalar_one()
        assert queue.schedule(conn, "k", {"a": 1}) is None  # pending: left alone
    _mark(db_engine, tid, queue.DONE)
    with db_engine.begin() as conn:
        assert queue.schedule(conn, "k", {"a": 1}) == queue.RERUN
        row = conn.execute(select(task)).one()
        assert (row.status, row.attempts, row.last_error) == (queue.PENDING, 0, None)
        assert conn.execute(select(row.next_run_at <= func.now())).scalar_one()


def test_schedule_only_reruns_done_tasks_older_than_cutoff(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        tid = queue.enqueue(conn, "k", {"a": 1})
        now = conn.execute(select(func.now())).scalar_one()
    assert tid is not None
    _mark(db_engine, tid, queue.DONE, now)
    with db_engine.begin() as conn:
        assert queue.schedule(conn, "k", {"a": 1}, done_before=now - timedelta(hours=1)) is None
        assert queue.schedule(conn, "k", {"a": 1}, done_before=now) == queue.RERUN
    _mark(db_engine, tid, queue.DEAD)
    with db_engine.begin() as conn:
        assert queue.schedule(conn, "k", {"a": 1}) is None  # dead tasks are not revived


def test_rerun_done_by_ids(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        maybe = [queue.enqueue(conn, "k", {"i": i}) for i in range(3)]
    ids = [i for i in maybe if i is not None]
    for tid, st in zip(ids, (queue.DONE, queue.DONE, queue.DEAD), strict=True):
        _mark(db_engine, tid, st)
    with db_engine.begin() as conn:
        assert sorted(queue.rerun_done(conn, ids)) == sorted(ids[:2])
        assert queue.rerun_done(conn, []) == []


def test_seconds_until_due(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        assert queue.seconds_until_due(conn) is None
        queue.enqueue(conn, "k", {"a": 1})
        conn.execute(update(task).values(next_run_at=func.now() + timedelta(seconds=30)))
        wait = queue.seconds_until_due(conn)
        assert wait is not None
        assert 29 < wait <= 30
        assert queue.seconds_until_due(conn, kinds=["other"]) is None
        conn.execute(update(task).values(next_run_at=func.now() - timedelta(seconds=30)))
        assert queue.seconds_until_due(conn) == 0


def test_rerun_done_skips_rows_locked_by_another_transaction(db_engine: Engine) -> None:
    """A row another transaction holds (e.g. a claimed task) is skipped, never waited on: worker
    slots share one event loop, so a blocking UPDATE there would deadlock the process."""
    with db_engine.begin() as conn:
        tid = queue.enqueue(conn, "k", {"a": 1})
    assert tid is not None
    _mark(db_engine, tid, queue.DONE)
    with db_engine.connect() as holder, holder.begin():
        holder.execute(select(task.c.id).where(task.c.id == tid).with_for_update())
        with db_engine.connect() as conn, conn.begin():
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert queue.rerun_done(conn, [tid]) == []
            assert queue.schedule(conn, "k", {"a": 1}) is None
    with db_engine.begin() as conn:
        assert queue.rerun_done(conn, [tid]) == [tid]


def test_enqueue_existing_task_claimed_elsewhere_does_not_block(db_engine: Engine) -> None:
    """``INSERT … ON CONFLICT DO NOTHING`` waits for an in-progress update of the conflicting row
    (e.g. a claim); enqueue checks for the committed row first, so it returns at once."""
    with db_engine.begin() as conn:
        queue.enqueue(conn, "k", {"a": 1})
    with db_engine.connect() as holder, holder.begin():
        assert len(queue.claim(holder, 1)) == 1
        with db_engine.connect() as conn, conn.begin():
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert queue.enqueue(conn, "k", {"a": 1}) is None
            assert queue.schedule(conn, "k", {"a": 1}) is None


def test_completing_child_while_parent_is_claimed_elsewhere_does_not_block(
    db_engine: Engine,
) -> None:
    """Updating a task row twice in one transaction (claim, then complete) re-checks its
    ``parent_id`` FK (``FOR KEY SHARE`` on the parent). Claims must lock ``FOR NO KEY UPDATE``,
    or a child finishing while another slot runs its parent deadlocks the worker (S18 run)."""
    with db_engine.begin() as conn:
        parent = queue.enqueue(conn, "parent", {})
        child = queue.enqueue(conn, "child", {}, parent_id=parent)
    with db_engine.connect() as holder, holder.begin():
        assert [t.id for t in queue.claim(holder, 1, kinds=["parent"])] == [parent]
        with db_engine.connect() as conn, conn.begin():
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert [t.id for t in queue.claim(conn, 1, kinds=["child"])] == [child]
            assert child is not None
            queue.complete(conn, child)
            assert queue.fail(conn, child, "x") == queue.PENDING
