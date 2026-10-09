"""U6: trade dashboard endpoints — ``GET /trade/periods``, ``/trade/summary``,
``/trade/breakdown``, ``/trade/series`` — read from ``mv_trade_rollup`` (migration 0011).
DB tests need Postgres."""

import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine, insert, text

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.trade import parse_range
from bps_fetcher.db.schema import hs_chapter, trade_flow
from bps_fetcher.rollup import refresh_trade_rollup
from bps_fetcher.settings import ApiSettings


@pytest.fixture
def api(db_engine: Engine) -> FastAPI:
    url = db_engine.url.render_as_string(hide_password=False)
    return create_app(ApiSettings(_env_file=None, database_url=url))


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c,
    ):
        yield c


TP, BPN, DUMAI = "TANJUNG PRIOK", "BALIKPAPAN", "DUMAI"


def _row(
    flow: int, year: int, month: int, hs2: str, port: str, country: str, value: str, kg: str | None
) -> dict[str, Any]:
    return {
        "flow": flow,
        "period_type": 2 if month == 0 else 1,
        "year": year,
        "month": month,
        "hs2": hs2,
        "port": port,
        "country": country,
        "value_usd": Decimal(value),
        "netweight_kg": None if kg is None else Decimal(kg),
    }


def seed(conn: Connection) -> None:
    """Exports: annual 2024 = 205 (deliberately != the monthly 2024 sum, 127); 2025 only monthly
    Jan-Mar (37). Imports: annual 2024 = 60 (monthly sum 50); 2025 monthly Jan-Feb (12)."""
    conn.execute(
        insert(hs_chapter),
        [
            {"hs2": "03", "description": "Fish", "source_flow": 1, "source_year": 2024},
            {"hs2": "15", "description": "Animal or vegetable fats", "source_flow": 1,
             "source_year": 2024},
            {"hs2": "27", "description": "Mineral fuels", "source_flow": 1, "source_year": 2024},
            {"hs2": "72", "description": "Iron and steel", "source_flow": 1, "source_year": 2024},
        ],
    )  # fmt: skip
    rows = [
        _row(1, 2024, 0, "27", TP, "CHINA", "100", "1000"),
        _row(1, 2024, 0, "27", BPN, "INDIA", "50", "500"),
        _row(1, 2024, 0, "15", DUMAI, "INDIA", "30", "300"),
        _row(1, 2024, 0, "72", TP, "CHINA", "20", "200"),
        _row(1, 2024, 0, "03", "", "", "5", None),
        *(_row(1, 2024, m, "27", TP, "CHINA", "10", "100") for m in range(1, 13)),
        _row(1, 2024, 3, "15", DUMAI, "INDIA", "7", "70"),
        *(_row(1, 2025, m, "27", TP, "CHINA", "11", "110") for m in range(1, 4)),
        _row(1, 2025, 2, "72", BPN, "INDIA", "4", "40"),
        _row(2, 2024, 0, "27", TP, "SINGAPORE", "60", "600"),
        *(_row(2, 2024, m, "27", TP, "SINGAPORE", "4", "40") for m in range(1, 13)),
        _row(2, 2024, 1, "72", TP, "CHINA", "2", "20"),
        *(_row(2, 2025, m, "27", TP, "SINGAPORE", "6", "60") for m in range(1, 3)),
    ]
    conn.execute(insert(trade_flow), rows)


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    with db_engine.begin() as conn:
        seed(conn)
    refresh_trade_rollup(db_engine, concurrently=False)
    return db_engine


@pytest.fixture
def empty(db_engine: Engine) -> Engine:
    refresh_trade_rollup(db_engine, concurrently=False)
    return db_engine


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _by_flow(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["flow"]: item for item in body["items"]}


# --- range parsing (no DB) -----------------------------------------------------------------------


def test_parse_range_year_and_month_modes() -> None:
    r = parse_range("2023", "2025")
    assert (r.granularity, r.start, r.end) == ("year", (2023, 1), (2025, 12))
    r = parse_range("2024-11", "2025-02")
    assert (r.granularity, r.start, r.end) == ("month", (2024, 11), (2025, 2))
    # A year on either side of a month range spans that whole year.
    r = parse_range("2024", "2025-02")
    assert (r.granularity, r.start, r.end) == ("month", (2024, 1), (2025, 2))
    assert parse_range("2024-03", None).end == (2024, 3)
    assert parse_range(None, "2024").start == (2024, 1)
    with pytest.raises(ValueError, match="after"):
        parse_range("2025", "2024")
    with pytest.raises(ValueError, match="YYYY"):
        parse_range("2024-13", None)


# --- /trade/periods ------------------------------------------------------------------------------


async def test_periods_lists_years_with_annual_flag_and_months(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/periods")
    assert body["items"] == [
        {"flow": "export", "year": 2024, "annual": True, "months": list(range(1, 13))},
        {"flow": "export", "year": 2025, "annual": False, "months": [1, 2, 3]},
        {"flow": "import", "year": 2024, "annual": True, "months": list(range(1, 13))},
        {"flow": "import", "year": 2025, "annual": False, "months": [1, 2]},
    ]
    assert body["latest_year"] == 2025
    assert body["latest_month"] == "2025-03"


async def test_periods_empty(empty: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/periods")
    assert body == {"items": [], "latest_year": None, "latest_month": None}


# --- /trade/summary ------------------------------------------------------------------------------


async def test_summary_whole_year_uses_annual_rows(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/summary", year=2024)
    assert body["range"] == {"start": "2024", "end": "2024", "granularity": "year"}
    items = _by_flow(body)
    assert items["export"]["value_usd"] == 205  # annual rows, not the monthly sum (127)
    assert items["export"]["netweight_kg"] == 2000
    assert items["export"]["periods"] == [{"year": 2024, "basis": "annual", "months": 12}]
    assert items["import"]["value_usd"] == 60
    assert body["balance_usd"] == 145


async def test_summary_year_without_annual_rows_sums_months_ytd(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/summary", year=2025)
    items = _by_flow(body)
    assert items["export"]["value_usd"] == 37
    assert items["export"]["periods"] == [{"year": 2025, "basis": "monthly", "months": 3}]
    assert items["import"]["value_usd"] == 12
    assert items["import"]["periods"] == [{"year": 2025, "basis": "monthly", "months": 2}]
    assert body["balance_usd"] == 25


async def test_summary_one_month(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary", year=2024, month=3)
    assert body["range"] == {"start": "2024-03", "end": "2024-03", "granularity": "month"}
    items = _by_flow(body)
    assert items["export"]["value_usd"] == 17
    assert items["export"]["periods"] == [{"year": 2024, "basis": "monthly", "months": 1}]
    assert items["import"]["value_usd"] == 4
    assert body["balance_usd"] == 13


async def test_summary_ranges(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary", **{"from": "2024-11", "to": "2025-02"})
    assert _by_flow(body)["export"]["value_usd"] == 10 + 10 + 11 + 11 + 4
    # Whole years: annual 2024 + monthly 2025.
    body = await _get(client, "/trade/summary", **{"from": "2024", "to": "2025"})
    export = _by_flow(body)["export"]
    assert export["value_usd"] == 205 + 37
    assert [p["basis"] for p in export["periods"]] == ["annual", "monthly"]


async def test_summary_one_flow_has_no_balance(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary", year=2024, flow="import")
    assert [i["flow"] for i in body["items"]] == ["import"]
    assert body["balance_usd"] is None


async def test_summary_defaults_to_latest_year(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary")
    assert body["range"]["start"] == "2025"


async def test_summary_without_data(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary", year=2019)
    for item in body["items"]:
        assert item["value_usd"] is None
        assert item["periods"] == []
    assert body["balance_usd"] is None


async def test_summary_empty_database(empty: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/summary")
    assert body["range"] is None
    assert all(i["value_usd"] is None for i in body["items"])


@pytest.mark.parametrize(
    "params",
    [
        {"month": 3},
        {"year": 2024, "from": "2024"},
        {"from": "2025", "to": "2024"},
        {"from": "2024-13"},
        {"year": 2024, "month": 13},
        {"flow": "both"},
    ],
)
async def test_summary_rejects_bad_params(
    seeded: Engine, client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    r = await client.get("/trade/summary", params=params)
    assert r.status_code == 422, r.text


# --- /trade/breakdown ----------------------------------------------------------------------------


async def test_breakdown_by_country_top_n_with_others(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/breakdown", by="country", top=1, **{"from": "2024"})
    assert body["by"] == "country"
    assert body["flow"] == "export"
    assert body["top"] == 1
    assert body["total"]["value_usd"] == 205
    assert body["items"] == [
        {"key": "CHINA", "label": "CHINA", "value_usd": 120, "netweight_kg": 1200,
         "share": pytest.approx(120 / 205)},
    ]  # fmt: skip
    assert body["others"] == {
        "count": 2,
        "value_usd": 85,
        "netweight_kg": 800,
        "share": pytest.approx(85 / 205),
    }


async def test_breakdown_by_hs2_labels_and_no_others(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/breakdown", by="hs2", **{"from": "2024"})
    assert [(i["key"], i["label"], i["value_usd"]) for i in body["items"]] == [
        ("27", "Mineral fuels", 150),
        ("15", "Animal or vegetable fats", 30),
        ("72", "Iron and steel", 20),
        ("03", "Fish", 5),
    ]
    assert body["others"] is None


async def test_breakdown_by_port_not_stated_has_no_label(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/breakdown", by="port", **{"from": "2024", "to": "2024"})
    assert [(i["key"], i["label"]) for i in body["items"]] == [
        (TP, TP), (BPN, BPN), (DUMAI, DUMAI), ("", None)
    ]  # fmt: skip


async def test_breakdown_month_range_and_imports(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/breakdown", by="port", **{"from": "2024-03", "to": "2024-03"})
    assert [(i["key"], i["value_usd"]) for i in body["items"]] == [(TP, 10), (DUMAI, 7)]
    assert body["range"] == {"start": "2024-03", "end": "2024-03", "granularity": "month"}
    body = await _get(
        client, "/trade/breakdown", by="country", flow="import", **{"from": "2024-01"}
    )
    assert [(i["key"], i["value_usd"]) for i in body["items"]] == [("SINGAPORE", 4), ("CHINA", 2)]


async def test_breakdown_defaults_and_empty(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/breakdown", by="hs2")
    assert body["range"]["start"] == "2025"  # latest year with data
    assert body["total"]["value_usd"] == 37
    body = await _get(client, "/trade/breakdown", by="hs2", **{"from": "2019"})
    assert body["items"] == []
    assert body["others"] is None
    assert body["total"]["value_usd"] is None


@pytest.mark.parametrize("params", [{"by": "region"}, {}, {"by": "hs2", "top": 0},
                                    {"by": "hs2", "top": 51}])  # fmt: skip
async def test_breakdown_rejects_bad_params(
    seeded: Engine, client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    r = await client.get("/trade/breakdown", params=params)
    assert r.status_code == 422, r.text


# --- /trade/series -------------------------------------------------------------------------------


def _points(series: dict[str, Any]) -> dict[str, float]:
    return {p["period"]: p["value_usd"] for p in series["points"]}


async def test_series_both_flows_and_balance(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/series")
    assert [s["flow"] for s in body["series"]] == ["export", "import"]
    export, imp = body["series"]
    assert len(export["points"]) == 15  # 2024-01 … 2025-03, monthly rows only
    assert export["points"][0] == {
        "period": "2024-01",
        "date": "2024-01-01",
        "value_usd": 10,
        "netweight_kg": 100,
    }
    assert _points(export)["2024-03"] == 17
    assert _points(imp)["2024-01"] == 6
    balance = {p["period"]: p["value_usd"] for p in body["balance"]}
    assert len(balance) == 14  # only months where both flows have data
    assert balance["2024-01"] == 4
    assert "2025-03" not in balance


async def test_series_filtered_by_chapter(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(client, "/trade/series", hs2="27")
    assert body["hs2"] == "27"
    assert body["hs2_label"] == "Mineral fuels"
    assert body["country"] is None
    _, imp = body["series"]
    assert _points(imp)["2024-01"] == 4  # the chapter-72 import is filtered out
    assert {p["period"]: p["value_usd"] for p in body["balance"]}["2024-01"] == 6


async def test_series_filtered_by_country_one_flow(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/series", country="INDIA", flow="export")
    assert [s["flow"] for s in body["series"]] == ["export"]
    assert _points(body["series"][0]) == {"2024-03": 7, "2025-02": 4}
    assert body["balance"] == []


async def test_series_filtered_by_chapter_and_country(
    seeded: Engine, client: httpx.AsyncClient
) -> None:
    body = await _get(client, "/trade/series", hs2="27", country="CHINA", **{"from": "2025"})
    export, imp = body["series"]
    assert _points(export) == {"2025-01": 11, "2025-02": 11, "2025-03": 11}
    assert imp["points"] == []


async def test_series_range(seeded: Engine, client: httpx.AsyncClient) -> None:
    body = await _get(
        client, "/trade/series", flow="import", **{"from": "2024-11", "to": "2025-01"}
    )
    assert list(_points(body["series"][0])) == ["2024-11", "2024-12", "2025-01"]


@pytest.mark.parametrize("params", [{"hs2": "7"}, {"from": "24"}, {"flow": "x"}])
async def test_series_rejects_bad_params(
    seeded: Engine, client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    r = await client.get("/trade/series", params=params)
    assert r.status_code == 422, r.text


# --- query plans ---------------------------------------------------------------------------------


def test_queries_read_the_rollup_index(seeded: Engine) -> None:
    from bps_fetcher.api.trade import availability, breakdown_query, scope

    with seeded.connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))  # tiny table: force the planner's hand
        rng = parse_range("2024-01", "2024-06")
        query = breakdown_query("country", 1, scope(rng, 1, availability(conn)).clause)
        sql = str(query.compile(conn, compile_kwargs={"literal_binds": True}))
        plan = "\n".join(r[0] for r in conn.execute(text(f"EXPLAIN {sql}")))
    assert "mv_trade_rollup" in plan
    assert "trade_flow" not in plan


# --- OpenAPI -------------------------------------------------------------------------------------


def test_openapi_operation_ids() -> None:
    paths = openapi_schema()["paths"]
    assert paths["/trade/periods"]["get"]["operationId"] == "getTradePeriods"
    assert paths["/trade/summary"]["get"]["operationId"] == "getTradeSummary"
    assert paths["/trade/breakdown"]["get"]["operationId"] == "getTradeBreakdown"
    assert paths["/trade/series"]["get"]["operationId"] == "getTradeSeries"
    assert paths["/trade/summary"]["get"]["tags"] == ["trade"]


# --- performance ---------------------------------------------------------------------------------

PERF_ROWS_PER_PERIOD = 3200  # x 2 flows x 12 years x 13 periods (annual + 12 months) = 998 400
PERF_BUDGET_S = 0.5

_PERF_DROP = [
    "ALTER TABLE trade_flow DROP CONSTRAINT fk_trade_flow_hs2_hs_chapter",
    "ALTER TABLE trade_flow DROP CONSTRAINT pk_trade_flow",
    "DROP INDEX ix_trade_flow_hs2_year",
    "DROP INDEX ix_trade_flow_country_year",
]
_PERF_INSERT = """
INSERT INTO trade_flow
    (flow, period_type, year, month, hs2, port, country, value_usd, netweight_kg)
SELECT f, CASE WHEN m = 0 THEN 2 ELSE 1 END, y, m,
       lpad((i % 98 + 1)::text, 2, '0'), 'PORT ' || (i % 60),
       'COUNTRY ' || (i % 211), (i % 997) * 1000.5, (i % 991) * 10
FROM generate_series(1, 2) f, generate_series(2014, 2025) y,
     generate_series(0, 12) m, generate_series(0, :n - 1) i
"""
_PERF_RESTORE = [
    "ALTER TABLE trade_flow ADD CONSTRAINT pk_trade_flow "
    "PRIMARY KEY (flow, period_type, year, month, hs2, port, country)",
    "CREATE INDEX ix_trade_flow_hs2_year ON trade_flow (hs2, year)",
    "CREATE INDEX ix_trade_flow_country_year ON trade_flow (country, year)",
    "ALTER TABLE trade_flow ADD CONSTRAINT fk_trade_flow_hs2_hs_chapter "
    "FOREIGN KEY (hs2) REFERENCES hs_chapter (hs2)",
]


@pytest.mark.perf
async def test_endpoints_fast_on_a_million_rows(db_engine: Engine, api: FastAPI) -> None:
    """~1M synthetic ``trade_flow`` rows (98 chapters x 60 ports x 211 countries mixed); every
    endpoint answers within the budget once the rollup is refreshed."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO hs_chapter SELECT lpad(c::text, 2, '0'), 'Chapter ' || c, 1, 2024 "
                "FROM generate_series(1, 98) c"
            )
        )
        # Bulk load: drop the keys, insert, rebuild them (3x faster than per-row maintenance).
        for statement in _PERF_DROP:
            conn.execute(text(statement))
        conn.execute(text(_PERF_INSERT), {"n": PERF_ROWS_PER_PERIOD})
        for statement in _PERF_RESTORE:
            conn.execute(text(statement))
        assert conn.execute(text("SELECT count(*) FROM trade_flow")).scalar_one() == 998_400
        conn.execute(text("ANALYZE trade_flow"))
    refresh_trade_rollup(db_engine, concurrently=False)
    with db_engine.begin() as conn:
        conn.execute(text("ANALYZE mv_trade_rollup"))

    requests: list[tuple[str, dict[str, Any]]] = [
        ("/trade/periods", {}),
        ("/trade/summary", {}),
        ("/trade/summary", {"from": "2014", "to": "2025"}),
        ("/trade/summary", {"from": "2014-01", "to": "2025-12"}),
        ("/trade/breakdown", {"by": "country", "from": "2014", "to": "2025"}),
        ("/trade/breakdown", {"by": "port", "from": "2014-01", "to": "2025-12", "top": 50}),
        ("/trade/breakdown", {"by": "hs2", "from": "2025-01", "to": "2025-06"}),
        ("/trade/series", {}),
        ("/trade/series", {"hs2": "27"}),
        ("/trade/series", {"country": "COUNTRY 7"}),
        ("/trade/series", {"hs2": "27", "country": "COUNTRY 7"}),
    ]
    try:
        async with (
            api.router.lifespan_context(api),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://test"
            ) as client,
        ):
            await client.get("/health")  # open the pool
            for path, params in requests:
                started = time.perf_counter()
                r = await client.get(path, params=params)
                elapsed = time.perf_counter() - started
                assert r.status_code == 200, r.text
                assert elapsed < PERF_BUDGET_S, f"{path} {params}: {elapsed * 1000:.0f} ms"
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("TRUNCATE trade_flow, hs_chapter"))
        refresh_trade_rollup(db_engine, concurrently=False)
