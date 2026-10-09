"""U3: ``GET /variables/{domain}/{var}`` (metadata + dimensions) and
``GET /variables/{domain}/{var}/series`` (time series per vervar x turvar, periods → dates).
DB tests need Postgres."""

from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, insert, text

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.series import MAX_SERIES, classify_turth, resolve_period, series_query
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


# --- period → date rules (pure) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("val", "label", "expected"),
    [
        (1, "Januari", ("month", 1)),
        (12, "Desember", ("month", 12)),
        (188, "Desember", ("month", 12)),  # non-standard val, label decides
        (13, "Tahunan", ("year", 0)),
        (0, "Tahun", ("year", 0)),
        (217, "Jumlah", ("year", 0)),
        (31, "Triwulan I", ("quarter", 1)),
        (34, "Triwulan IV", ("quarter", 4)),
        (213, "triwulan 3", ("quarter", 3)),
        (61, "Semester 1 (Maret)", ("semester", 1)),
        (62, "Semester 2 (September)", ("semester", 2)),
        (329, "Semester II", ("semester", 2)),
        (261, "Januari_I", ("other", 0)),
        (5, "Mystery", ("month", 5)),  # unknown label: standard BPS codes 1-12 = months
        (13, "Mystery", ("year", 0)),
        (500, "Mystery", ("other", 0)),
    ],
)
def test_classify_turth(val: int, label: str, expected: tuple[str, int]) -> None:
    assert classify_turth(val, label) == expected


@pytest.mark.parametrize(
    ("year_label", "turth", "turth_label", "expected"),
    [
        ("2024", 3, "Maret", ("month", "2024-03", date(2024, 3, 1))),
        ("2024", 13, "Tahunan", ("year", "2024", date(2024, 1, 1))),
        ("2023", 0, "Tahun", ("year", "2023", date(2023, 1, 1))),
        ("2024", 32, "Triwulan II", ("quarter", "2024-Q2", date(2024, 4, 1))),
        ("2024", 61, "Semester 1 (Maret)", ("semester", "2024-S1", date(2024, 3, 1))),
        ("2024", 62, "Semester 2 (September)", ("semester", "2024-S2", date(2024, 9, 1))),
        ("2024", 330, "Semester 2", ("semester", "2024-S2", date(2024, 7, 1))),
        ("2024", 261, "Januari_I", ("other", "2024 Januari_I", None)),
        ("2019/2020", 0, "Tahun", ("year", "2019/2020", None)),
        (None, 3, "Maret", ("month", "th125 Maret", None)),
    ],
)
def test_resolve_period(
    year_label: str | None,
    turth: int,
    turth_label: str,
    expected: tuple[str, str, date | None],
) -> None:
    p = resolve_period(125, year_label, turth, turth_label)
    assert (p.freq, p.period, p.date) == expected


# --- seeded variable ----------------------------------------------------------------------------

MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
          "September", "Oktober", "November", "Desember"]  # fmt: skip


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    """0000/2263: monthly inflation for 2 regions x 1 category, 2023-2024 (13 = Tahunan, empty);
    0000/1804: annual, 3 regions x 2 categories."""
    with db_engine.begin() as conn:
        conn.execute(insert(domain), [{"domain_id": "0000", "name": "Indonesia", "level": "pusat"}])
        conn.execute(
            insert(variable),
            [
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "title": "Inflasi Tahunan (Y-on-Y)",
                    "unit": "Persen",
                    "sub_id": 3,
                    "sub_name": "Inflasi",
                    "subcsa_name": "Harga-Harga",
                    "def": "<p>Inflasi adalah <b>kenaikan</b> harga</p>",
                    "notes": "<p>Mulai 2024 (2022=100)</p><script>alert(1)</script>",
                    "decimal": 2,
                    "last_update": datetime(2026, 10, 1, 11, 24, 1),
                },
            ],
        )
        conn.execute(
            insert(variable),
            [{"domain_id": "0000", "var_id": 1804, "title": "Korban Bencana", "unit": "Orang"}],
        )
        conn.execute(
            insert(period),
            [
                {"domain_id": "0000", "var_id": 2263, "th_id": 123, "label": "2023"},
                {"domain_id": "0000", "var_id": 2263, "th_id": 124, "label": "2024"},
                {"domain_id": "0000", "var_id": 1804, "th_id": 118, "label": "2018"},
                {"domain_id": "0000", "var_id": 1804, "th_id": 119, "label": "2019"},
            ],
        )
        conn.execute(
            insert(dim_vervar),
            [
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "val": 9999,
                    "label": "INDONESIA",
                    "group_label": "38 Provinsi",
                },
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "val": 1100,
                    "label": "PROV ACEH",
                    "group_label": "38 Provinsi",
                },
                *[
                    {
                        "domain_id": "0000",
                        "var_id": 1804,
                        "val": v,
                        "label": f"R{v}",
                        "group_label": None,
                    }
                    for v in (1, 2, 3)
                ],
            ],
        )
        conn.execute(
            insert(dim_turvar),
            [
                {"domain_id": "0000", "var_id": 2263, "val": 0, "label": "Tidak ada"},
                {"domain_id": "0000", "var_id": 1804, "val": 10, "label": "Meninggal"},
                {"domain_id": "0000", "var_id": 1804, "val": 11, "label": "Hilang"},
            ],
        )
        conn.execute(
            insert(dim_turth),
            [
                *[
                    {"domain_id": "0000", "var_id": 2263, "val": m, "label": MONTHS[m - 1]}
                    for m in range(1, 13)
                ],
                {"domain_id": "0000", "var_id": 2263, "val": 13, "label": "Tahunan"},
                {"domain_id": "0000", "var_id": 1804, "val": 0, "label": "Tahun"},
            ],
        )
        obs = [
            {"domain_id": "0000", "var_id": 2263, "vervar": vv, "turvar": 0, "th": th,
             "turth": m, "value": Decimal(f"{vv % 7}.{th - 120}{m:02d}")}
            for vv in (9999, 1100) for th in (124, 123) for m in range(12, 0, -1)
        ] + [
            {"domain_id": "0000", "var_id": 1804, "vervar": vv, "turvar": tv, "th": th,
             "turth": 0, "value": Decimal(vv * 100 + tv + th)}
            for vv in (1, 2, 3) for tv in (10, 11) for th in (118, 119)
        ]  # fmt: skip
        conn.execute(insert(observation), obs)
    return db_engine


# --- detail -------------------------------------------------------------------------------------


async def test_variable_detail(client: httpx.AsyncClient, seeded: Engine) -> None:
    r = await client.get("/variables/0000/2263")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: body[k] for k in body if k not in ("vervars", "turvars", "turths", "periods")} == {
        "domain_id": "0000",
        "domain_name": "Indonesia",
        "domain_level": "pusat",
        "var_id": 2263,
        "title": "Inflasi Tahunan (Y-on-Y)",
        "unit": "Persen",
        "subject_id": 3,
        "subject": "Inflasi",
        "category": "Harga-Harga",
        "definition": "<p>Inflasi adalah <b>kenaikan</b> harga</p>",
        "notes": "<p>Mulai 2024 (2022=100)</p><script>alert(1)</script>",
        "decimal": 2,
        "last_update": "2026-10-01T11:24:01",
    }
    assert body["vervars"] == [
        {"val": 1100, "label": "PROV ACEH", "group_label": "38 Provinsi"},
        {"val": 9999, "label": "INDONESIA", "group_label": "38 Provinsi"},
    ]
    assert body["turvars"] == [{"val": 0, "label": "Tidak ada"}]
    assert body["turths"][0] == {"val": 1, "label": "Januari", "freq": "month", "has_data": True}
    assert body["turths"][-1] == {"val": 13, "label": "Tahunan", "freq": "year", "has_data": False}
    assert len(body["turths"]) == 13
    assert body["periods"] == [{"th": 123, "label": "2023"}, {"th": 124, "label": "2024"}]


async def test_variable_detail_minimal(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = (await client.get("/variables/0000/1804")).json()
    assert body["definition"] is None
    assert body["notes"] is None
    assert body["last_update"] is None
    assert [t["freq"] for t in body["turths"]] == ["year"]
    assert [v["val"] for v in body["turvars"]] == [10, 11]


@pytest.mark.parametrize("path", ["/variables/0000/9", "/variables/9999/2263"])
async def test_variable_detail_unknown(
    client: httpx.AsyncClient, seeded: Engine, path: str
) -> None:
    for p in (path, f"{path}/series"):
        r = await client.get(p)
        assert r.status_code == 404, p
        assert r.json() == {"detail": "Variable not found"}


async def test_variable_detail_bad_domain(client: httpx.AsyncClient) -> None:
    assert (await client.get("/variables/00/2263")).status_code == 422


# --- series -------------------------------------------------------------------------------------


async def _series(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_series_monthly_sorted_with_dates(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _series(
        client, "/variables/0000/2263/series", vervar=[9999, 1100], turth=list(range(1, 13))
    )
    assert body["truncated"] is False
    assert body["max_series"] == MAX_SERIES
    labels = [
        (s["vervar"], s["vervar_label"], s["turvar"], s["turvar_label"]) for s in body["series"]
    ]
    assert labels == [
        (9999, "INDONESIA", 0, "Tidak ada"),
        (1100, "PROV ACEH", 0, "Tidak ada"),
    ]  # fmt: skip
    points = body["series"][0]["points"]
    assert len(points) == 24
    assert points[0] == {
        "period": "2023-01",
        "date": "2023-01-01",
        "th": 123,
        "turth": 1,
        "value": 3.301,
    }
    assert points[-1]["period"] == "2024-12"
    assert [p["date"] for p in points] == sorted(p["date"] for p in points)


async def test_series_defaults_to_every_combination(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    body = await _series(client, "/variables/0000/1804/series")
    keys = [(s["vervar"], s["turvar"]) for s in body["series"]]
    assert keys == [(1, 10), (1, 11), (2, 10), (2, 11), (3, 10), (3, 11)]
    first = body["series"][0]
    assert first["turvar_label"] == "Meninggal"
    assert [(p["period"], p["date"], p["value"]) for p in first["points"]] == [
        ("2018", "2018-01-01", 228.0),
        ("2019", "2019-01-01", 229.0),
    ]


async def test_series_filters_turvar_and_turth(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _series(client, "/variables/0000/1804/series", vervar=2, turvar=11)
    assert [(s["vervar"], s["turvar"]) for s in body["series"]] == [(2, 11)]
    body = await _series(client, "/variables/0000/2263/series", vervar=9999, turth=[3, 13])
    [s] = body["series"]
    assert [p["period"] for p in s["points"]] == ["2023-03", "2024-03"]


async def test_series_unknown_member_is_an_empty_series(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    body = await _series(client, "/variables/0000/2263/series", vervar=4242)
    assert body["series"] == [
        {"vervar": 4242, "vervar_label": None, "turvar": 0, "turvar_label": "Tidak ada",
         "points": []}
    ]  # fmt: skip


async def test_series_cap(client: httpx.AsyncClient, seeded: Engine) -> None:
    many = list(range(1, MAX_SERIES + 5))
    body = await _series(client, "/variables/0000/1804/series", vervar=many, turvar=10)
    assert body["truncated"] is True
    assert len(body["series"]) == MAX_SERIES
    assert [s["vervar"] for s in body["series"]] == many[:MAX_SERIES]


async def test_series_dedups_requested_members(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _series(client, "/variables/0000/1804/series", vervar=[2, 2, 1], turvar=10)
    assert [s["vervar"] for s in body["series"]] == [2, 1]


def test_series_query_uses_an_observation_index(seeded: Engine) -> None:
    stmt = series_query("0000", 2263, vervars=[9999], turvars=[0], turths=[1, 2, 3])
    with seeded.connect() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = stmt.compile(seeded, compile_kwargs={"literal_binds": True})
        plan = "\n".join(conn.execute(text(f"EXPLAIN {compiled}")).scalars())
    assert "Seq Scan on observation" not in plan, plan
    assert "pk_observation" in plan or "ix_observation_domain_id_var_id_th" in plan, plan


# --- OpenAPI ------------------------------------------------------------------------------------


def test_openapi_detail_and_series_operations() -> None:
    schema = openapi_schema()
    detail = schema["paths"]["/variables/{domain}/{var}"]["get"]
    assert detail["operationId"] == "getVariable"
    assert detail["tags"] == ["variables"]
    series = schema["paths"]["/variables/{domain}/{var}/series"]["get"]
    assert series["operationId"] == "getVariableSeries"
    names = {p["name"] for p in series["parameters"]}
    assert names == {"domain", "var", "vervar", "turvar", "turth"}
    assert {"VariableDetail", "SeriesResponse", "Series", "SeriesPoint"} <= schema["components"][
        "schemas"
    ].keys()
