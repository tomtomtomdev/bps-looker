"""S16: ``trade`` handler — parse ``dataexim/`` responses into ``trade_flow``/``hs_chapter``,
scope-replacing reloads, ``seed_trade`` (fake client serving the recorded 2024 chapter-03 export
responses; DB tests need Postgres)."""

import copy
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, func, select

from bps_fetcher import queue, worker
from bps_fetcher.db.schema import hs_chapter, raw_response, task, trade_flow
from bps_fetcher.handlers.trade import (
    ANNUAL_MONTH,
    TradeScope,
    parse_trade,
    scope_of,
    seed_trade,
    trade,
    upsert_hs_chapters,
)
from bps_fetcher.trade import EXPORT, IMPORT, MONTHLY, YEARLY
from bps_fetcher.worker import TaskContext

UNAVAILABLE = {"status": "OK", "data-availability": "unavailable"}
FISH = "Fish, crustaceans and mollusca"
ANNUAL_TOTAL = Decimal("3995628247.491")

Bodies = dict[tuple[int, int], dict[str, Any]]


class FakeClient:
    """Serves ``dataexim/`` by ``(sumber, periode)``; records calls."""

    def __init__(self, bodies: Bodies) -> None:
        self.bodies = bodies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "dataexim/"
        return copy.deepcopy(self.bodies.get((params["sumber"], params["periode"]), UNAVAILABLE))


@pytest.fixture
def annual(fixture_body: Callable[[str], Any]) -> dict[str, Any]:
    return copy.deepcopy(fixture_body("trade_exp_annual_03_2024"))


@pytest.fixture
def monthly(fixture_body: Callable[[str], Any]) -> dict[str, Any]:
    return copy.deepcopy(fixture_body("trade_exp_monthly_03_2024"))


def _params(
    flow: int = EXPORT, period_type: int = YEARLY, year: int = 2024, chapters: str = "03"
) -> dict[str, Any]:
    return {"flow": flow, "period_type": period_type, "year": year, "chapters": chapters}


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="trade", params=params, attempts=0, parent_id=None)


async def _handle(engine: Engine, bodies: Bodies, params: dict[str, Any]) -> worker.HandlerResult:
    with engine.begin() as conn:
        return await trade(TaskContext(_task(params), FakeClient(bodies), conn))


def _count(engine: Engine, *where: Any) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(select(func.count()).select_from(trade_flow).where(*where)).scalar_one()
        )


def _chapters(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        return {r.hs2: r for r in conn.execute(select(hs_chapter)).all()}


# --- params / parsing (no DB) --------------------------------------------------------------------


def test_registered() -> None:
    assert worker.HANDLERS["trade"] is trade


def test_scope_of_validates_params() -> None:
    assert scope_of(_params(chapters="01;02;98")) == TradeScope(
        EXPORT, YEARLY, 2024, ("01", "02", "98")
    )
    for bad in (
        _params(flow=3),
        _params(period_type=0),
        _params(year=2013),
        _params(chapters=""),
        _params(chapters="3"),
        _params(chapters="03;03"),
        {"flow": 1, "period_type": 2, "year": 2024},
        {**_params(), "year": "2024"},
    ):
        with pytest.raises(ValueError, match="trade"):
            scope_of(bad)
    # extra params (e.g. a ``run`` label) are ignored
    assert scope_of({**_params(), "run": "2026-10-09"}).year == 2024


def test_parse_annual(annual: dict[str, Any]) -> None:
    parsed = parse_trade(annual, scope_of(_params()))
    assert len(parsed.rows) == 686
    assert parsed.descriptions == {"03": FISH}
    assert {r["month"] for r in parsed.rows} == {ANNUAL_MONTH}
    first = parsed.rows[0]
    assert first == {
        "flow": EXPORT,
        "period_type": YEARLY,
        "year": 2024,
        "month": 0,
        "hs2": "03",
        "port": "BELAWAN",
        "country": "ALBANIA",
        "value_usd": Decimal(2129015),
        "netweight_kg": Decimal(403430),
    }
    assert all(isinstance(r["value_usd"], Decimal) for r in parsed.rows)
    assert sum(r["value_usd"] for r in parsed.rows) == ANNUAL_TOTAL
    # one row has no port (``pod: null``) -> empty-string sentinel, PK columns are NOT NULL
    assert [r["country"] for r in parsed.rows if r["port"] == ""] != []


def test_parse_monthly(monthly: dict[str, Any]) -> None:
    parsed = parse_trade(monthly, scope_of(_params(period_type=MONTHLY)))
    assert len(parsed.rows) == 4590
    assert {r["month"] for r in parsed.rows} == set(range(1, 13))
    # floats keep their decimal digits exactly
    assert Decimal("186697.5") in {r["value_usd"] for r in parsed.rows}
    assert sum(r["value_usd"] for r in parsed.rows) == ANNUAL_TOTAL
    assert sum(1 for r in parsed.rows if r["port"] == "") == 6


def test_parse_rejects_rows_outside_the_scope(
    annual: dict[str, Any], monthly: dict[str, Any]
) -> None:
    def bad(body: dict[str, Any], scope: TradeScope, **change: Any) -> None:
        b = copy.deepcopy(body)
        b["data"][0].update(change)
        with pytest.raises(ValueError, match=r"trade row 0|month out of range"):
            parse_trade(b, scope)

    yearly = scope_of(_params())
    by_month = scope_of(_params(period_type=MONTHLY))
    bad(annual, yearly, kodehs="[04] Dairy produce")  # chapter not requested
    bad(annual, yearly, tahun="2023")
    bad(annual, yearly, bulan="[01] Januari")  # annual row with a month
    bad(annual, yearly, ctr=42)
    bad(monthly, by_month, bulan=None)
    bad(monthly, by_month, bulan="[13] Tahunan")
    bad(annual, yearly, value="lots")
    # a monthly response asked for as annual is caught too
    with pytest.raises(ValueError, match="annual row has"):
        parse_trade(monthly, yearly)


@pytest.mark.parametrize("ctr", [None, ""])
def test_parse_missing_country_is_empty_sentinel(annual: dict[str, Any], ctr: Any) -> None:
    """2025/2026 exports have a few rows with ``ctr: null`` (e.g. HS 84/87 via Tanjung Priok,
    seen 2026-10-09) — kept, like ``pod: null``, with the ``''`` sentinel (PK is NOT NULL)."""
    body = copy.deepcopy(annual)
    body["data"][0]["ctr"] = ctr
    parsed = parse_trade(body, scope_of(_params()))
    assert len(parsed.rows) == 686
    assert parsed.rows[0]["country"] == ""
    assert sum(r["value_usd"] for r in parsed.rows) == ANNUAL_TOTAL


def test_parse_sums_duplicate_keys(annual: dict[str, Any]) -> None:
    body = copy.deepcopy(annual)
    body["data"] = body["data"][:2]
    body["data"].append(dict(body["data"][0], value=1.5, netweight=None))
    parsed = parse_trade(body, scope_of(_params()))
    assert len(parsed.rows) == 2
    assert parsed.rows[0]["value_usd"] == Decimal("2129016.5")
    assert parsed.rows[0]["netweight_kg"] == Decimal(403430)


def test_parse_unavailable_is_empty() -> None:
    parsed = parse_trade(UNAVAILABLE, scope_of(_params()))
    assert parsed.rows == []
    assert parsed.descriptions == {}
    assert not parsed.available


# --- loading -------------------------------------------------------------------------------------


async def test_handler_loads_annual_and_monthly(
    db_engine: Engine, annual: dict[str, Any], monthly: dict[str, Any]
) -> None:
    bodies = {(EXPORT, YEARLY): annual, (EXPORT, MONTHLY): monthly}
    client = FakeClient(bodies)
    with db_engine.begin() as conn:
        result = await trade(TaskContext(_task(_params()), client, conn))
    assert client.calls == [
        ("dataexim/", {"sumber": 1, "periode": 2, "kodehs": "03", "jenishs": 1, "tahun": 2024})
    ]
    assert [(r.endpoint, dict(r.params)) for r in result.raw] == [
        ("dataexim/", {"sumber": 1, "periode": 2, "kodehs": "03", "jenishs": 1, "tahun": 2024})
    ]
    assert result.raw[0].body == annual
    await _handle(db_engine, bodies, _params(period_type=MONTHLY))

    assert _count(db_engine, trade_flow.c.period_type == YEARLY) == 686
    assert _count(db_engine, trade_flow.c.period_type == MONTHLY) == 4590
    assert _count(db_engine, trade_flow.c.period_type == YEARLY, trade_flow.c.month != 0) == 0
    with db_engine.connect() as conn:
        total = conn.execute(
            select(func.sum(trade_flow.c.value_usd)).where(trade_flow.c.period_type == MONTHLY)
        ).scalar()
        row = conn.execute(
            select(trade_flow).where(
                trade_flow.c.period_type == MONTHLY,
                trade_flow.c.month == 11,
                trade_flow.c.port == "BELAWAN",
                trade_flow.c.country == "ALBANIA",
            )
        ).one()
    assert total == ANNUAL_TOTAL
    assert (row.value_usd, row.netweight_kg) == (Decimal(95600), Decimal(20000))
    assert row.fetched_at is not None
    ch = _chapters(db_engine)
    assert set(ch) == {"03"}
    assert (ch["03"].description, ch["03"].source_flow, ch["03"].source_year) == (
        FISH,
        EXPORT,
        2024,
    )


async def test_reload_replaces_scope_only(
    db_engine: Engine, annual: dict[str, Any], monthly: dict[str, Any]
) -> None:
    await _handle(db_engine, {(EXPORT, MONTHLY): monthly}, _params(period_type=MONTHLY))
    await _handle(db_engine, {(EXPORT, YEARLY): annual}, _params())
    imports = copy.deepcopy(monthly)
    await _handle(db_engine, {(IMPORT, MONTHLY): imports}, _params(IMPORT, MONTHLY))

    revised = copy.deepcopy(monthly)
    revised["data"] = revised["data"][:100]
    revised["data"][0]["value"] = 123456789.25
    await _handle(db_engine, {(EXPORT, MONTHLY): revised}, _params(period_type=MONTHLY))

    export_monthly = (trade_flow.c.flow == EXPORT, trade_flow.c.period_type == MONTHLY)
    assert _count(db_engine, *export_monthly) == 100  # vanished rows are gone
    assert (
        _count(db_engine, *export_monthly, trade_flow.c.value_usd == Decimal("123456789.25")) == 1
    )
    assert _count(db_engine, trade_flow.c.period_type == YEARLY) == 686  # other scopes untouched
    assert _count(db_engine, trade_flow.c.flow == IMPORT) == 4590


async def test_reload_keeps_other_chapter_batches(
    db_engine: Engine, annual: dict[str, Any]
) -> None:
    other = copy.deepcopy(annual)
    for r in other["data"]:
        r["kodehs"] = "[04] Dairy produce"
    await _handle(db_engine, {(EXPORT, YEARLY): annual}, _params(chapters="01;02;03"))
    await _handle(db_engine, {(EXPORT, YEARLY): other}, _params(chapters="04;05"))
    await _handle(db_engine, {(EXPORT, YEARLY): annual}, _params(chapters="01;02;03"))
    assert _count(db_engine, trade_flow.c.hs2 == "03") == 686
    assert _count(db_engine, trade_flow.c.hs2 == "04") == 686


async def test_failed_reload_through_worker_keeps_old_rows(
    db_engine: Engine, annual: dict[str, Any]
) -> None:
    await _handle(db_engine, {(EXPORT, YEARLY): annual}, _params())
    broken = copy.deepcopy(annual)
    broken["data"][-1]["tahun"] = "1999"
    with db_engine.begin() as conn:
        queue.enqueue(conn, "trade", _params())
    stats = await worker.run_worker(
        db_engine, client=FakeClient({(EXPORT, YEARLY): broken}), kinds=["trade"], idle_sleep=0
    )
    assert (stats.done, stats.failed) == (0, 1)
    assert _count(db_engine) == 686
    with db_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(raw_response)).scalar() == 0


async def test_unavailable_loads_nothing_and_keeps_rows(
    db_engine: Engine, annual: dict[str, Any]
) -> None:
    await _handle(db_engine, {(EXPORT, YEARLY): annual}, _params())
    result = await _handle(db_engine, {}, _params())
    assert [r.body for r in result.raw] == [UNAVAILABLE]
    assert _count(db_engine) == 686  # an unavailable answer never wipes loaded data

    result = await _handle(db_engine, {}, _params(year=2014, chapters="77"))
    assert _count(db_engine, trade_flow.c.year == 2014) == 0


def test_hs_chapter_prefers_latest_year_then_export(db_engine: Engine) -> None:
    def put(desc: str, flow: int, year: int) -> None:
        with db_engine.begin() as conn:
            upsert_hs_chapters(conn, {"03": desc}, flow=flow, year=year)

    def desc() -> tuple[str, int, int]:
        r = _chapters(db_engine)["03"]
        return r.description, r.source_flow, r.source_year

    put("Ikan dan krustasea", IMPORT, 2014)
    assert desc() == ("Ikan dan krustasea", IMPORT, 2014)
    put("Fish, crustaceans", EXPORT, 2014)  # same year: export (English) wins over import
    assert desc() == ("Fish, crustaceans", EXPORT, 2014)
    put("Ikan dan krustasea", IMPORT, 2014)  # ...and is not overwritten by it again
    assert desc() == ("Fish, crustaceans", EXPORT, 2014)
    put("Fish, crustaceans and molluscs", IMPORT, 2020)  # a later year wins
    assert desc() == ("Fish, crustaceans and molluscs", IMPORT, 2020)
    put("Fish (old)", EXPORT, 2019)  # an older year never does
    assert desc() == ("Fish, crustaceans and molluscs", IMPORT, 2020)
    put("Fish, crustaceans and mollusca", IMPORT, 2020)  # same rank: latest fetch wins
    assert desc() == ("Fish, crustaceans and mollusca", IMPORT, 2020)


# --- seeding + end to end ------------------------------------------------------------------------


def test_seed_trade_enqueues_chapter_batches(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        n = seed_trade(conn, 2023, 2024)
        assert n == 2 * 2 * 2 * 10  # flows x period types x years x batches of 10 (98 chapters)
        assert seed_trade(conn, 2023, 2024) == 0  # idempotent
        assert seed_trade(conn, 2024, 2024, run="2026-10-09") == 40
        params = [
            r.params
            for r in conn.execute(select(task).where(task.c.kind == "trade").order_by(task.c.id))
        ]
    first = params[0]
    assert first == {
        "flow": 1,
        "period_type": 1,
        "year": 2023,
        "chapters": "01;02;03;04;05;06;07;08;09;10",
    }
    assert params[-1]["run"] == "2026-10-09"
    assert all(scope_of(p) for p in params)  # every seeded task is valid for the handler
    covered = {c for p in params[:10] for c in p["chapters"].split(";")}
    assert len(covered) == 98
    assert "77" not in covered

    with db_engine.begin() as conn:
        assert (
            seed_trade(conn, 2024, 2024, flows=[IMPORT], period_types=[YEARLY], batch_size=50) == 2
        )
    for bad in ((2013, 2014), (2024, 2023)):
        with (
            db_engine.begin() as conn,
            pytest.raises(ValueError, match=r"starts in|empty year range"),
        ):
            seed_trade(conn, *bad)
    with db_engine.begin() as conn, pytest.raises(ValueError, match="bad trade flows"):
        seed_trade(conn, 2024, 2024, flows=[3])


async def test_end_to_end_through_worker(
    db_engine: Engine, annual: dict[str, Any], monthly: dict[str, Any]
) -> None:
    with db_engine.begin() as conn:
        n = seed_trade(conn, 2024, 2024, flows=[EXPORT], batch_size=98)
    assert n == 2
    with db_engine.begin() as conn:  # a batch the API has nothing for
        queue.enqueue(conn, "trade", _params(IMPORT, YEARLY, 2014, "77"))
    # the recorded responses only hold chapter 03, which is in the seeded (all-chapter) batch
    client = FakeClient({(EXPORT, YEARLY): annual, (EXPORT, MONTHLY): monthly})

    stats = await worker.run_worker(db_engine, client=client, kinds=["trade"], idle_sleep=0)

    assert (stats.done, stats.failed) == (3, 0)
    assert len(client.calls) == 3
    assert _count(db_engine) == 686 + 4590
    with db_engine.connect() as conn:
        assert [t.status for t in conn.execute(select(task))] == ["done"] * 3
        raws = conn.execute(select(raw_response).order_by(raw_response.c.id)).all()
    assert len(raws) == 3
    assert raws[0].endpoint == "dataexim/"
    assert raws[0].params["kodehs"].startswith("01;02;03")
    assert raws[2].body == UNAVAILABLE
    assert set(_chapters(db_engine)) == {"03"}
