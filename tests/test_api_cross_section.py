"""U4: ``GET /variables/{domain}/{var}/cross-section`` — one value per region for one period
(ranking + map). DB tests need Postgres."""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, insert, text

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.series import cross_section_query
from bps_fetcher.db.schema import (
    dim_turth,
    dim_turvar,
    dim_vervar,
    domain,
    observation,
    period,
    variable,
)
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


REGIONS = {1100: "PROV ACEH", 3100: "PROV DKI JAKARTA", 9200: "PROV PAPUA BARAT DAYA",
           9999: "INDONESIA"}  # fmt: skip
MONTHS = {1: "Januari", 2: "Februari", 3: "Maret", 13: "Tahunan"}


def _rows(table: Any, var_id: int, rows: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    return insert(table), [{"domain_id": "0000", "var_id": var_id, **r} for r in rows]


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    """0000/2263 monthly: 3 provinces + national, 2024 Jan-Mar, 2025 Jan-Feb (9200 has no Feb
    2025 value; 13 = Tahunan has none). 0000/500: 2 categories, quarters + annual, 2 regions."""
    with db_engine.begin() as conn:
        conn.execute(insert(domain), [{"domain_id": "0000", "name": "Indonesia", "level": "pusat"}])
        conn.execute(
            insert(variable),
            [
                {"domain_id": "0000", "var_id": 2263, "title": "Inflasi", "unit": "Persen"},
                {"domain_id": "0000", "var_id": 500, "title": "PDRB", "unit": "Rp"},
            ],
        )
        for stmt, rows in (
            _rows(period, 2263, [{"th_id": 124, "label": "2024"}, {"th_id": 125, "label": "2025"}]),
            _rows(period, 500, [{"th_id": 124, "label": "2024"}]),
            _rows(dim_vervar, 2263, [{"val": v, "label": lb} for v, lb in REGIONS.items()]),
            _rows(
                dim_vervar,
                500,
                [{"val": 1100, "label": "<b>ACEH</b>"}, {"val": 1200, "label": "SUMUT"}],
            ),
            _rows(dim_turvar, 2263, [{"val": 0, "label": "Tidak ada"}]),
            _rows(dim_turvar, 500, [{"val": 1, "label": "ADHB"}, {"val": 2, "label": "ADHK"}]),
            _rows(dim_turth, 2263, [{"val": m, "label": lb} for m, lb in MONTHS.items()]),
            _rows(
                dim_turth,
                500,
                [
                    {"val": 31, "label": "Triwulan I"},
                    {"val": 32, "label": "Triwulan II"},
                    {"val": 35, "label": "Tahunan"},
                ],
            ),
        ):
            conn.execute(stmt, rows)
        obs = [
            {"vervar": vv, "turvar": 0, "th": th, "turth": m,
             "value": Decimal(vv) / 1000 + th - 124 + Decimal(m) / 10}
            for vv in REGIONS for th, months in ((124, (1, 2, 3)), (125, (1, 2))) for m in months
            if not (vv == 9200 and th == 125 and m == 2)
        ]  # fmt: skip
        obs += [
            {"vervar": vv, "turvar": tv, "th": 124, "turth": t, "value": Decimal(vv + tv + t)}
            for vv in (1100, 1200) for tv in (1, 2) for t in (31, 32, 35)
        ]  # fmt: skip
        conn.execute(
            insert(observation),
            [{"domain_id": "0000", "var_id": 2263 if r["turvar"] == 0 else 500, **r} for r in obs],
        )
    return db_engine


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _ranking(body: dict[str, Any]) -> list[tuple[int, float | None]]:
    return [(r["vervar"], r["value"]) for r in body["regions"]]


async def test_defaults_to_latest_period(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/variables/0000/2263/cross-section")
    assert body["turvar"] == 0
    assert body["turvar_label"] == "Tidak ada"
    assert body["period"] == {
        "th": 125,
        "turth": 2,
        "period": "2025-02",
        "date": "2025-02-01",
        "label": "Februari 2025",
    }
    # Sorted by value desc; a region without a value for the period comes last as null.
    assert _ranking(body) == [(3100, 4.3), (1100, 2.3), (9200, None)]
    assert body["regions"][0]["label"] == "PROV DKI JAKARTA"
    # The national aggregate isn't ranked: it comes separately.
    assert body["national"] == {"vervar": 9999, "label": "INDONESIA", "value": 11.199}


async def test_periods_for_the_slider(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/variables/0000/2263/cross-section")
    periods = [(p["th"], p["turth"], p["period"]) for p in body["periods"]]
    # Only (th, turth) pairs with data, in time order (13 = Tahunan has none).
    assert periods == [
        (124, 1, "2024-01"),
        (124, 2, "2024-02"),
        (124, 3, "2024-03"),
        (125, 1, "2025-01"),
        (125, 2, "2025-02"),
    ]


async def test_explicit_period(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/variables/0000/2263/cross-section", th=124, turth=3)
    assert body["period"]["period"] == "2024-03"
    assert _ranking(body) == [(9200, 9.5), (3100, 3.4), (1100, 1.4)]
    # th alone → its latest sub-period; turth alone → the latest year that has it.
    body = await _get(client, "/variables/0000/2263/cross-section", th=124)
    assert body["period"]["period"] == "2024-03"
    body = await _get(client, "/variables/0000/2263/cross-section", turth=3)
    assert body["period"]["period"] == "2024-03"


async def test_period_without_data(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/variables/0000/2263/cross-section", th=125, turth=13)
    assert body["period"]["period"] == "2025"
    assert all(r["value"] is None for r in body["regions"])
    assert body["national"]["value"] is None
    body = await _get(client, "/variables/0000/2263/cross-section", th=999)
    assert body["period"] is None
    assert all(r["value"] is None for r in body["regions"])


async def test_freq_and_turvar(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/variables/0000/500/cross-section", turvar=2, freq="quarter")
    assert body["turvar_label"] == "ADHK"
    assert [p["period"] for p in body["periods"]] == ["2024-Q1", "2024-Q2"]
    assert body["period"]["period"] == "2024-Q2"
    assert body["period"]["label"] == "Triwulan II 2024"
    assert _ranking(body) == [(1200, 1234.0), (1100, 1134.0)]
    assert body["national"] is None
    body = await _get(client, "/variables/0000/500/cross-section", freq="year")
    assert body["turvar"] == 1
    assert body["period"]["period"] == "2024"
    assert body["period"]["label"] == "2024"
    assert _ranking(body) == [(1200, 1236.0), (1100, 1136.0)]


async def test_unknown_variable(client: httpx.AsyncClient, seeded: Engine) -> None:
    r = await client.get("/variables/0000/9/cross-section")
    assert r.status_code == 404
    assert r.json() == {"detail": "Variable not found"}
    r = await client.get("/variables/0000/2263/cross-section", params={"freq": "decade"})
    assert r.status_code == 422


def test_cross_section_query_uses_an_observation_index(seeded: Engine) -> None:
    stmt = cross_section_query("0000", 2263, turvar=0, th=124, turth=3)
    with seeded.connect() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = stmt.compile(seeded, compile_kwargs={"literal_binds": True})
        plan = "\n".join(conn.execute(text(f"EXPLAIN {compiled}")).scalars())
    assert "Seq Scan on observation" not in plan, plan


def test_openapi_cross_section_operation() -> None:
    schema = openapi_schema()
    op = schema["paths"]["/variables/{domain}/{var}/cross-section"]["get"]
    assert op["operationId"] == "getVariableCrossSection"
    assert op["tags"] == ["variables"]
    assert {p["name"] for p in op["parameters"]} == {"domain", "var", "th", "turvar", "turth",
                                                     "freq"}  # fmt: skip
    assert {"CrossSection", "CrossSectionRegion", "CrossSectionPeriod"} <= schema["components"][
        "schemas"
    ].keys()
