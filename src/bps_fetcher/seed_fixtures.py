"""Seed a database from the recorded BPS fixtures (``tests/fixtures``) — no API calls.

    bps seed-fixtures [tests/fixtures]

Used by the UI stack e2e (U7): the real handlers run through the queue + worker with a
:class:`FixtureClient` that answers each request with the recorded body whose path + params
match, and ``data-availability: not-available`` for anything that was not recorded (what BPS
answers for an empty list). Deterministic: the same fixtures always give the same rows, and a
second run is a no-op (tasks are idempotent on their params).

What gets loaded: every domain (``domain_all``), the first var-list page of ``0000``, periods of
var 1804, observations of 1804 (2017-2019) and 2263 (inflation y-on-y, 38 provinces, 2024), the 16
national strategic indicators, and chapter-03 exports 2024 (annual + monthly); then the trade
rollup view is refreshed.
"""

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from bps_fetcher import queue
from bps_fetcher.handlers.periods import ThItem, upsert_periods
from bps_fetcher.handlers.variables import VarItem, upsert_variables
from bps_fetcher.paginate import NOT_AVAILABLE
from bps_fetcher.rollup import refresh_trade_rollup
from bps_fetcher.trade import EXPORT, MONTHLY, YEARLY
from bps_fetcher.worker import run_worker

log = logging.getLogger(__name__)

DEFAULT_FIXTURES_DIR = Path("tests/fixtures")
DOMAIN = "0000"
TRADE_CHAPTERS = "03"
TRADE_YEAR = 2024

# The successful recordings the seed is built from (``error_*`` and the null / not-available
# data cases are left out: they exist to test failure handling).
SEED_FIXTURES = (
    "domain_all",
    "var_0000_p1",
    "th_0000_1804",
    "data_0000_1804",
    "data_0000_2263",
    "indicators_0000_p1",
    "indicators_0000_p2",
    "trade_exp_annual_03_2024",
    "trade_exp_monthly_03_2024",
)
# Variables whose data is recorded but which are not on the recorded var-list page.
DATA_FIXTURES = {1804: "data_0000_1804", 2263: "data_0000_2263"}


class MissingFixtureError(FileNotFoundError):
    """A fixture the seed needs is not in the directory."""


def _key(path: str, params: Mapping[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Match on path + stringified params; ``page=1`` is the default (some recordings omit it)."""
    items = {k: str(v) for k, v in params.items()}
    if items.get("page") == "1":
        del items["page"]
    return path, tuple(sorted(items.items()))


def _not_available() -> dict[str, Any]:
    return {"status": "OK", "data-availability": NOT_AVAILABLE}


@dataclass
class FixtureClient:
    """Async stand-in for :class:`~bps_fetcher.client.BpsClient` over recorded responses."""

    bodies: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, Any]]
    served: set[str] = field(default_factory=set)
    misses: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def from_dir(cls, directory: Path, names: Iterable[str] = SEED_FIXTURES) -> "FixtureClient":
        bodies: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, Any]] = {}
        for name in names:
            path = Path(directory) / f"{name}.json"
            if not path.is_file():
                raise MissingFixtureError(f"fixture {name!r} not found in {directory}")
            recorded = json.loads(path.read_text(encoding="utf-8"))
            request = recorded["request"]
            bodies[_key(request["path"], request["params"])] = (name, recorded["body"])
        return cls(bodies)

    @property
    def names(self) -> list[str]:
        return sorted(name for name, _ in self.bodies.values())

    def body(self, name: str) -> Any:
        return next(body for n, body in self.bodies.values() if n == name)

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        hit = self.bodies.get(_key(path, params))
        if hit is None:
            self.misses.append((path, dict(params)))
            return _not_available()
        name, body = hit
        self.served.add(name)
        result: dict[str, Any] = json.loads(json.dumps(body))  # handlers get a fresh copy
        return result


@dataclass(frozen=True, slots=True)
class SeedResult:
    done: int
    failed: int
    unused: list[str]
    """Seed fixtures no handler asked for (empty unless the handlers' requests changed)."""


def _data_variables(client: FixtureClient) -> list[VarItem]:
    """Variable rows for the recorded data bodies (normally written by ``var_list``)."""
    items = []
    for var_id, name in DATA_FIXTURES.items():
        meta = client.body(name)["var"][0]
        items.append(
            VarItem.model_validate(
                {
                    "var_id": var_id,
                    "title": meta["label"],
                    "unit": meta.get("unit"),
                    "sub_name": meta.get("subj"),
                    "def": meta.get("def"),
                    "notes": meta.get("note"),
                }
            )
        )
    return items


def _work(engine: Engine, client: FixtureClient) -> tuple[int, int]:
    stats = asyncio.run(run_worker(engine, client=client, concurrency=1, drain=True))
    return stats.done, stats.failed


def seed_fixtures(engine: Engine, directory: Path = DEFAULT_FIXTURES_DIR) -> SeedResult:
    """Load the seed fixtures into the (migrated) database behind ``engine``."""
    client = FixtureClient.from_dir(directory)

    # 1. Domains (all rows) -> var list of 0000 -> th lists (only 1804's is recorded).
    with engine.begin() as conn:
        queue.enqueue(conn, "domains", {"domains": [DOMAIN]})
    done, failed = _work(engine, client)

    # 2. The recorded data windows, indicators and trade (their tasks need step 1's rows).
    with engine.begin() as conn:
        upsert_variables(conn, DOMAIN, _data_variables(client))
        for var_id, name in DATA_FIXTURES.items():  # period labels (normally from th_list)
            years = client.body(name)["tahun"]
            upsert_periods(
                conn,
                DOMAIN,
                var_id,
                (ThItem.model_validate({"th_id": t["val"], "th": t["label"]}) for t in years),
            )
        queue.enqueue(conn, "th_list", {"domain": DOMAIN, "var": 1804})
        queue.enqueue(conn, "data", {"domain": DOMAIN, "var": 1804, "th": "117:119"})
        queue.enqueue(conn, "data", {"domain": DOMAIN, "var": 2263, "th": "124"})
        queue.enqueue(conn, "indicators", {"domain": DOMAIN})
        for period_type in (YEARLY, MONTHLY):
            queue.enqueue(
                conn,
                "trade",
                {
                    "flow": EXPORT,
                    "period_type": period_type,
                    "year": TRADE_YEAR,
                    "chapters": TRADE_CHAPTERS,
                },
            )
    more_done, more_failed = _work(engine, client)
    refresh_trade_rollup(engine)

    unused = sorted(set(client.names) - client.served)
    log.info(
        "seeded from fixtures: %d task(s) done, %d failed, %d unrecorded request(s)",
        done + more_done,
        failed + more_failed,
        len(client.misses),
    )
    return SeedResult(done + more_done, failed + more_failed, unused)
