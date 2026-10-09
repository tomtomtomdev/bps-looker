"""Refresh of ``mv_trade_rollup`` (migration 0011), the trade dashboard's pre-aggregated totals.

Trade rows only change through ``trade`` tasks, so the worker refreshes the rollup after them
(:func:`trade_refresher`, wired into ``bps work``): at most every ``interval`` seconds while a run
goes on, and once more when the worker stops — the scheduler's ``bps work --drain`` therefore
leaves it fresh. ``bps refresh-views`` refreshes by hand (e.g. after loading rows another way).

``CONCURRENTLY`` (the default) builds the new contents next to the old and swaps the difference
in, so API readers are never blocked; it only takes a lock that excludes another refresh.
"""

import logging
import time

from sqlalchemy import Engine, text

from bps_fetcher.worker import Refresher

log = logging.getLogger(__name__)

TRADE_ROLLUP = "mv_trade_rollup"
ROLLUP_KINDS = frozenset({"trade"})
DEFAULT_REFRESH_INTERVAL = 600.0
# Enough to aggregate the grouping sets in memory (hash) instead of sorting on disk:
# 1M rows refresh in ~2 s instead of ~5 s. Session-local (SET LOCAL), only for the refresh.
REFRESH_WORK_MEM = "256MB"


def refresh_trade_rollup(engine: Engine, *, concurrently: bool = True) -> float:
    """Recompute ``mv_trade_rollup`` from ``trade_flow``; returns the seconds it took."""
    started = time.perf_counter()
    mode = " CONCURRENTLY" if concurrently else ""
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL work_mem = '{REFRESH_WORK_MEM}'"))
        conn.execute(text(f"REFRESH MATERIALIZED VIEW{mode} {TRADE_ROLLUP}"))
    elapsed = time.perf_counter() - started
    log.info("refreshed %s in %.1f s", TRADE_ROLLUP, elapsed)
    return elapsed


def trade_refresher(interval: float = DEFAULT_REFRESH_INTERVAL) -> Refresher:
    """The worker hook: refresh the rollup after ``trade`` tasks completed."""
    return Refresher(ROLLUP_KINDS, refresh_trade_rollup, interval=interval)
