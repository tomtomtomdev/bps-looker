"""S14: ``indicators`` handler + ``indicator_snapshot`` history (fake client serving the two
recorded national pages; DB tests need Postgres)."""

import copy
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select

from bps_fetcher import queue, worker
from bps_fetcher.db.schema import domain, indicator_snapshot, raw_response, task
from bps_fetcher.handlers.indicators import (
    IndicatorItem,
    indicators,
    seed_indicators,
    upsert_snapshots,
)
from bps_fetcher.worker import TaskContext

NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeClient:
    """Serves ``/list?model=indicators`` pages (page number -> body); records calls."""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "list"
        assert params["model"] == "indicators"
        return self.pages[int(params["page"])]


@pytest.fixture
def pages(fixture_body: Callable[[str], Any]) -> dict[int, dict[str, Any]]:
    return {
        1: copy.deepcopy(fixture_body("indicators_0000_p1")),
        2: copy.deepcopy(fixture_body("indicators_0000_p2")),
    }


def _items(pages: dict[int, dict[str, Any]]) -> list[IndicatorItem]:
    return [IndicatorItem.model_validate(i) for p in pages.values() for i in p["data"][1]]


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="indicators", params=params, attempts=0, parent_id=None)


def _seed_domains(engine: Engine, *rows: tuple[str, str]) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(domain),
            [{"domain_id": d, "name": f"D{d}", "level": lvl} for d, lvl in rows],
        )


def _rows(engine: Engine) -> dict[tuple[str, int, str, str], Any]:
    with engine.connect() as conn:
        return {
            (r.domain_id, r.indicator_id, r.periode, r.title): r
            for r in conn.execute(select(indicator_snapshot)).all()
        }


async def _handle(
    engine: Engine, pages: dict[int, dict[str, Any]], params: dict[str, Any]
) -> tuple[FakeClient, worker.HandlerResult]:
    client = FakeClient(pages)
    with engine.begin() as conn:
        result = await indicators(TaskContext(_task(params), client, conn))
    return client, result


# --- pure ----------------------------------------------------------------------------------------


def test_parses_both_fixture_pages(pages: dict[int, dict[str, Any]]) -> None:
    items = _items(pages)
    assert len(items) == 16
    assert len({i.indicator_id for i in items}) == 16
    infl = items[0]
    assert (infl.indicator_id, infl.var, infl.subject_csa) == (3, 2263, 536)
    assert infl.title == "Inflasi Year on Year, September 2026"
    assert infl.value == Decimal("3.28")
    assert (infl.unit, infl.periode, infl.category, infl.data_source) == (
        "Persen",
        "September 2026",
        2,
        "BPS",
    )
    wisman = next(i for i in items if i.indicator_id == 25)
    assert wisman.value == Decimal("1599660")
    assert wisman.data_source == "Badan Pusat Statistik"


def test_item_value_and_blank_handling() -> None:
    base = {"indicator_id": 1, "title": "T", "periode": "2025", "value": 0.1}
    assert IndicatorItem.model_validate(base).value == Decimal("0.1")
    assert IndicatorItem.model_validate({**base, "value": "12.5"}).value == Decimal("12.5")
    for odd in (None, "", "-", "n/a"):
        assert IndicatorItem.model_validate({**base, "value": odd}).value is None
    item = IndicatorItem.model_validate({**base, "unit": "", "name": "", "extra": 1})
    assert (item.unit, item.name) == (None, None)
    bad = dict(base)
    del bad["indicator_id"]
    with pytest.raises(ValidationError):
        IndicatorItem.model_validate(bad)


def test_registered_in_handlers() -> None:
    assert worker.HANDLERS["indicators"] is indicators


# --- snapshot upsert -----------------------------------------------------------------------------


def test_upsert_keeps_first_seen_bumps_last_seen_and_is_idempotent(
    db_engine: Engine, pages: dict[int, dict[str, Any]]
) -> None:
    _seed_domains(db_engine, ("0000", "pusat"))
    items = _items(pages)
    with db_engine.begin() as conn:
        assert upsert_snapshots(conn, "0000", items, seen_at=T0) == 16
    first = _rows(db_engine)
    assert len(first) == 16
    assert all(r.first_seen == T0 and r.last_seen == T0 for r in first.values())

    later = T0 + timedelta(days=1)
    with db_engine.begin() as conn:
        assert upsert_snapshots(conn, "0000", items, seen_at=later) == 0  # no new rows
    second = _rows(db_engine)
    assert set(second) == set(first)
    for key, r in second.items():
        assert r.first_seen == T0
        assert r.last_seen == later
        assert dict(r._mapping) | {"last_seen": T0} == dict(first[key]._mapping)

    # An older sighting (e.g. a retried stale task) never moves last_seen back.
    with db_engine.begin() as conn:
        upsert_snapshots(conn, "0000", items, seen_at=T0)
    assert all(r.last_seen == later for r in _rows(db_engine).values())


def test_new_periode_adds_history_row(db_engine: Engine, pages: dict[int, dict[str, Any]]) -> None:
    _seed_domains(db_engine, ("0000", "pusat"))
    with db_engine.begin() as conn:
        upsert_snapshots(conn, "0000", _items(pages), seen_at=T0)

    nxt = copy.deepcopy(pages)
    infl = nxt[1]["data"][1][0]
    infl.update(title="Inflasi Year on Year, Oktober 2026", periode="Oktober 2026", value=3.1)
    later = T0 + timedelta(days=30)
    with db_engine.begin() as conn:
        assert upsert_snapshots(conn, "0000", _items(nxt), seen_at=later) == 1

    rows = _rows(db_engine)
    assert len(rows) == 17
    history = sorted((r for r in rows.values() if r.indicator_id == 3), key=lambda r: r.first_seen)
    assert [(r.periode, r.value, r.first_seen, r.last_seen) for r in history] == [
        ("September 2026", Decimal("3.28"), T0, T0),
        ("Oktober 2026", Decimal("3.1"), later, later),
    ]


def test_revised_value_same_periode_updates_in_place(
    db_engine: Engine, pages: dict[int, dict[str, Any]]
) -> None:
    _seed_domains(db_engine, ("0000", "pusat"))
    with db_engine.begin() as conn:
        upsert_snapshots(conn, "0000", _items(pages), seen_at=T0)
    rev = copy.deepcopy(pages)
    rev[1]["data"][1][0]["value"] = 3.3
    with db_engine.begin() as conn:
        upsert_snapshots(conn, "0000", _items(rev), seen_at=T0 + timedelta(hours=1))
    r = _rows(db_engine)[("0000", 3, "September 2026", "Inflasi Year on Year, September 2026")]
    assert (r.value, r.first_seen) == (Decimal("3.3"), T0)
    assert len(_rows(db_engine)) == 16


# --- handler -------------------------------------------------------------------------------------


async def test_handler_pages_both_fixtures_and_stores_raw_per_page(
    db_engine: Engine, pages: dict[int, dict[str, Any]]
) -> None:
    _seed_domains(db_engine, ("0000", "pusat"))
    client, result = await _handle(db_engine, pages, {"domain": "0000"})

    assert client.calls == [
        ("list", {"model": "indicators", "domain": "0000", "page": 1}),
        ("list", {"model": "indicators", "domain": "0000", "page": 2}),
    ]
    assert [(r.endpoint, dict(r.params), r.body) for r in result.raw] == [
        ("list", {"model": "indicators", "domain": "0000", "page": 1}, pages[1]),
        ("list", {"model": "indicators", "domain": "0000", "page": 2}, pages[2]),
    ]
    assert result.children == []
    rows = _rows(db_engine)
    assert len(rows) == 16
    ntp = rows[("0000", 22, "September 2026", "Nilai Tukar Petani, September 2026")]
    assert (ntp.var, ntp.value, ntp.unit, ntp.category) == (
        1741,
        Decimal("131.11"),
        "Tidak Ada Satuan",
        2,
    )
    assert ntp.first_seen == ntp.last_seen


async def test_prov_domain_is_fetched(db_engine: Engine) -> None:
    _seed_domains(db_engine, ("3100", "prov"))
    page = {
        "status": "OK",
        "data-availability": "available",
        "data": [
            {"page": 1, "pages": 1, "per_page": 10, "count": 1, "total": 1},
            [{"indicator_id": 9, "var": 1, "title": "X, 2025", "periode": "2025", "value": 1}],
        ],
    }
    _, result = await _handle(db_engine, {1: page}, {"domain": "3100"})
    assert set(_rows(db_engine)) == {("3100", 9, "2025", "X, 2025")}
    assert len(result.raw) == 1


async def test_kab_domain_is_skipped_without_calling_api(db_engine: Engine) -> None:
    _seed_domains(db_engine, ("3171", "kab"))
    client, result = await _handle(db_engine, {}, {"domain": "3171"})
    assert client.calls == []
    assert result.raw == []
    assert result.children == []
    assert _rows(db_engine) == {}


async def test_not_available_stores_raw_only(db_engine: Engine) -> None:
    _seed_domains(db_engine, ("0000", "pusat"))
    _, result = await _handle(db_engine, {1: NOT_AVAILABLE}, {"domain": "0000"})
    assert [r.body for r in result.raw] == [NOT_AVAILABLE]
    assert _rows(db_engine) == {}


async def test_bad_params_or_missing_domain_row(db_engine: Engine) -> None:
    with pytest.raises(ValueError, match="domain"):
        await _handle(db_engine, {}, {})
    with pytest.raises(ValueError, match="malformed"):
        await _handle(db_engine, {}, {"domain": "abc"})
    with pytest.raises(LookupError, match="0000"):
        await _handle(db_engine, {}, {"domain": "0000"})


# --- seeding -------------------------------------------------------------------------------------


def _task_params(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(task.c.params).where(task.c.kind == "indicators").order_by(task.c.id)
        ).all()
    return [r.params for r in rows]


def test_seed_all_pusat_and_prov_domains(db_engine: Engine) -> None:
    _seed_domains(db_engine, ("0000", "pusat"), ("3100", "prov"), ("1100", "prov"), ("3171", "kab"))
    with db_engine.begin() as conn:
        assert seed_indicators(conn) == 3
    assert _task_params(db_engine) == [{"domain": "0000"}, {"domain": "1100"}, {"domain": "3100"}]
    with db_engine.begin() as conn:
        assert seed_indicators(conn) == 0  # idempotent


def test_seed_given_domains_and_rejects_kab(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        assert seed_indicators(conn, ["0000"]) == 1
    assert _task_params(db_engine) == [{"domain": "0000"}]
    with pytest.raises(ValueError, match="3171"), db_engine.begin() as conn:
        seed_indicators(conn, ["3100", "3171"])
    assert _task_params(db_engine) == [{"domain": "0000"}]


def test_seed_run_label_makes_a_fresh_snapshot_task(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        assert seed_indicators(conn, ["0000"], run="2026-10-09") == 1
        assert seed_indicators(conn, ["0000"], run="2026-10-10") == 1
        assert seed_indicators(conn, ["0000"], run="2026-10-10") == 0
    assert _task_params(db_engine) == [
        {"domain": "0000", "run": "2026-10-09"},
        {"domain": "0000", "run": "2026-10-10"},
    ]


# --- end to end through the worker ---------------------------------------------------------------


async def test_end_to_end_through_worker(
    db_engine: Engine, pages: dict[int, dict[str, Any]]
) -> None:
    _seed_domains(db_engine, ("0000", "pusat"), ("3171", "kab"))
    with db_engine.begin() as conn:
        seed_indicators(conn, ["0000"])
        kab = queue.enqueue(conn, "indicators", {"domain": "3171"})
    client = FakeClient(pages)

    stats = await worker.run_worker(db_engine, client=client, kinds=["indicators"], idle_sleep=0.01)

    assert (stats.done, stats.failed) == (2, 0)
    assert len(client.calls) == 2
    assert len(_rows(db_engine)) == 16
    with db_engine.connect() as conn:
        tasks = conn.execute(select(task).order_by(task.c.id)).all()
        raws = conn.execute(select(raw_response).order_by(raw_response.c.id)).all()
    assert [t.status for t in tasks] == ["done", "done"]
    nat = tasks[0].id
    assert kab == tasks[1].id
    assert [(r.task_id, r.endpoint, r.params["page"]) for r in raws] == [
        (nat, "list", 1),
        (nat, "list", 2),
    ]
    assert raws[1].body == pages[2]
