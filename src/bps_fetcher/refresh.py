"""Incremental refresh: re-run what was already crawled once its policy interval has elapsed.

``refresh(conn, now=...)`` (CLI: ``bps seed refresh``) only **re-pends done tasks** (or enqueues
the canonical task when it doesn't exist yet) — it never invents run labels, so it stays idempotent
on ``(kind, params_hash)``: a re-pended task is ``pending`` again, and a second refresh in a row
finds nothing ``done`` and due, so it schedules nothing. A task's ``updated_at`` is its completion
time; it is "due" once ``updated_at <= now - interval``. Re-pended tasks get ``attempts = 0`` and
``next_run_at = now()`` (the worker picks them up right away). Refresh only keeps fresh what was
seeded before — widening the crawl is the job of the ``bps seed …`` commands.

Policies (by ``source``):

- ``indicators`` — daily: one canonical ``{"domain": d}`` task for every domain that has any
  ``indicators`` task (run-labelled S14 tasks included).
- ``trade`` — weekly: the previous and current year (never before 2014) for every
  flow x period type x chapter batch seen in any ``trade`` task; missing years are enqueued.
- ``dynamic``:
  - ``var_list`` — weekly re-list of each domain's variables. New variables fan out to
    ``th_list`` → ``data`` as usual; ``th_list`` of known variables already exist, so the
    re-list costs only its list pages.
  - ``data_probe`` — the variable list has no change stamp, so each variable is probed by
    re-running its **latest data window** (highest period; split S13 parents excluded) — the
    window most likely to be revised. If the response's ``last_update`` is newer than the stored
    one, the ``data`` handler re-pends the variable's other windows and its ``th_list`` (new
    periods only add/extend the last window). Probe interval by how recently the variable last
    changed (``variable.last_update``): within :data:`HOT_AGE` → weekly, within
    :data:`WARM_AGE` → every 4 weeks, older or never loaded → every 13 weeks. Variables whose
    latest window isn't ``done`` (pending, running, dead) are skipped.

    **Staggered** (S19): a crawl completes all its windows within hours, so plain "done more than
    one interval ago" would make every probe due on the same day. Instead each variable has a
    fixed phase (:func:`probe_phase`, a hash of domain + var id) within its interval, and a probe
    is due once a phase boundary ``epoch + phase*every + k*every`` lies in ``(done, now]``. So the
    first probes after a crawl are spread evenly over one interval, a probe is never due later
    than one interval after the window completed, and a var probed at its boundary keeps it
    (no drift). The cost: a var completed just before its boundary is probed again soon after.

``--domain`` scopes ``indicators``, ``var_list`` and ``data_probe``; trade is national.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final

from sqlalchemy import BigInteger, Connection, Float, Integer, case, func, literal, select
from sqlalchemy.dialects.postgresql import distinct_on

from bps_fetcher import queue
from bps_fetcher.db.schema import task, variable
from bps_fetcher.handlers.data import window_tasks
from bps_fetcher.handlers.indicators import KIND as INDICATORS_KIND
from bps_fetcher.handlers.trade import KIND as TRADE_KIND
from bps_fetcher.handlers.variables import KIND as VAR_LIST_KIND
from bps_fetcher.trade import EARLIEST_YEAR

SOURCES: Final = ("indicators", "trade", "dynamic")
POLICIES: Final = ("indicators", "trade", "var_list", "data_probe")
_SOURCE_POLICIES: Final = {
    "indicators": ("indicators",),
    "trade": ("trade",),
    "dynamic": ("var_list", "data_probe"),
}

INDICATORS_EVERY: Final = timedelta(days=1)
TRADE_EVERY: Final = timedelta(days=7)
VAR_LIST_EVERY: Final = timedelta(days=7)
HOT_AGE: Final = timedelta(days=90)
WARM_AGE: Final = timedelta(days=365)
PROBE_HOT_EVERY: Final = timedelta(days=7)
PROBE_WARM_EVERY: Final = timedelta(days=28)
PROBE_COLD_EVERY: Final = timedelta(days=91)

# ``variable.last_update`` is BPS wall-clock time (WIB, stored naive).
BPS_TZ: Final = timezone(timedelta(hours=7))


_PHASE_MULT: Final = 2654435761  # Knuth's multiplicative hash
_PHASE_MOD: Final = 2**32
_PHASE_DOMAIN_MULT: Final = 65599


def probe_phase(domain_id: str, var_id: int) -> float:
    """A variable's fixed position in ``[0, 1)`` of its probe interval (deterministic, spread).

    Mirrored in SQL by :func:`_phase_sql`; both use bigint-safe integer arithmetic."""
    key = int(domain_id) * _PHASE_DOMAIN_MULT + var_id
    return (key * _PHASE_MULT) % _PHASE_MOD / _PHASE_MOD


def _phase_sql(domain_id: Any, var_id: Any) -> Any:
    key = domain_id.cast(BigInteger) * _PHASE_DOMAIN_MULT + var_id.cast(BigInteger)
    return ((key * _PHASE_MULT) % _PHASE_MOD).cast(Float) / float(_PHASE_MOD)


def _staggered_due(done_at: Any, now: datetime, every: Any, phase: Any) -> Any:
    """SQL: a phase boundary (``phase`` x ``every`` + k x ``every`` after the epoch) lies in
    ``(done_at, now]``."""
    every_s = func.extract("epoch", every)
    offset = phase * every_s
    return func.floor((literal(now.timestamp()) - offset) / every_s) > func.floor(
        (func.extract("epoch", done_at) - offset) / every_s
    )


def _domain_filter(domains: Sequence[str] | None) -> Any:
    if domains is None:
        return literal(True)
    return task.c.params["domain"].astext.in_(list(domains))


def refresh_indicators(conn: Connection, now: datetime, domains: Sequence[str] | None) -> int:
    seeded = conn.execute(
        select(task.c.params["domain"].astext)
        .where(task.c.kind == INDICATORS_KIND, _domain_filter(domains))
        .distinct()
    ).scalars()
    done_before = now - INDICATORS_EVERY
    return sum(
        queue.schedule(conn, INDICATORS_KIND, {"domain": d}, done_before=done_before) is not None
        for d in sorted(seeded)
    )


def refresh_trade(conn: Connection, now: datetime) -> int:
    p = task.c.params
    combos = conn.execute(
        select(p["flow"].as_integer(), p["period_type"].as_integer(), p["chapters"].astext)
        .where(task.c.kind == TRADE_KIND)
        .distinct()
    ).all()
    years = range(max(EARLIEST_YEAR, now.year - 1), now.year + 1)
    done_before = now - TRADE_EVERY
    n = 0
    for flow, period_type, chapters in sorted(tuple(c) for c in combos):
        for year in years:
            params = {"flow": flow, "period_type": period_type, "year": year, "chapters": chapters}
            if queue.schedule(conn, TRADE_KIND, params, done_before=done_before) is not None:
                n += 1
    return n


def refresh_var_lists(conn: Connection, now: datetime, domains: Sequence[str] | None) -> int:
    ids = conn.execute(
        select(task.c.id).where(task.c.kind == VAR_LIST_KIND, _domain_filter(domains))
    ).scalars()
    return len(queue.rerun_done(conn, ids, done_before=now - VAR_LIST_EVERY))


def latest_windows() -> Any:
    """Per variable, the ``data`` window task with the highest period (ties: the narrowest)."""
    th = task.c.params["th"].astext
    windows = (
        window_tasks()
        .add_columns(
            task.c.status,
            task.c.updated_at,
            task.c.params["domain"].astext.label("domain_id"),
            task.c.params["var"].as_integer().label("var_id"),
            func.coalesce(func.nullif(func.split_part(th, ":", 2), ""), th)
            .cast(Integer)
            .label("upper"),
            func.split_part(th, ":", 1).cast(Integer).label("lower"),
        )
        .subquery("windows")
    )
    w = windows.c
    return (
        select(w.id, w.status, w.updated_at, w.domain_id, w.var_id)
        .ext(distinct_on(w.domain_id, w.var_id))
        .order_by(w.domain_id, w.var_id, w.upper.desc(), w.lower.desc(), w.id.desc())
        .subquery("latest")
    )


def refresh_data_probes(conn: Connection, now: datetime, domains: Sequence[str] | None) -> int:
    latest = latest_windows()
    local_now = now.astimezone(BPS_TZ).replace(tzinfo=None)
    every = case(
        (variable.c.last_update >= local_now - HOT_AGE, PROBE_HOT_EVERY),
        (variable.c.last_update >= local_now - WARM_AGE, PROBE_WARM_EVERY),
        else_=PROBE_COLD_EVERY,
    )
    q = (
        select(latest.c.id)
        .join(
            variable,
            (variable.c.domain_id == latest.c.domain_id) & (variable.c.var_id == latest.c.var_id),
            isouter=True,
        )
        .where(
            latest.c.status == queue.DONE,
            _staggered_due(
                latest.c.updated_at, now, every, _phase_sql(latest.c.domain_id, latest.c.var_id)
            ),
        )
    )
    if domains is not None:
        q = q.where(latest.c.domain_id.in_(list(domains)))
    return len(queue.rerun_done(conn, conn.execute(q).scalars()))


def refresh(
    conn: Connection,
    *,
    now: datetime | None = None,
    domains: Iterable[str] | None = None,
    sources: Iterable[str] = SOURCES,
) -> dict[str, int]:
    """Apply the refresh policies of ``sources`` as of ``now`` (default: the current time);
    returns how many tasks each policy scheduled (new or re-pended)."""
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    doms = None if domains is None else sorted(set(domains))
    wanted = list(dict.fromkeys(sources))
    for s in wanted:
        if s not in SOURCES:
            raise ValueError(f"unknown refresh source {s!r} (choose from {', '.join(SOURCES)})")
    policies = [p for p in POLICIES if any(p in _SOURCE_POLICIES[s] for s in wanted)]
    out: dict[str, int] = {}
    for policy in policies:
        if policy == "indicators":
            out[policy] = refresh_indicators(conn, now, doms)
        elif policy == "trade":
            out[policy] = refresh_trade(conn, now)
        elif policy == "var_list":
            out[policy] = refresh_var_lists(conn, now, doms)
        else:
            out[policy] = refresh_data_probes(conn, now, doms)
    return out
