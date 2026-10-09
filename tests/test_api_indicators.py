"""U5: ``GET /indicators`` (latest snapshot per strategic indicator + change vs the previous
periode) and ``GET /indicators/{domain}/{id}/history``. DB tests need Postgres."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, insert

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.indicators import indicator_label
from bps_fetcher.db.schema import domain, indicator_snapshot, variable
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


def _day(d: int) -> datetime:
    return datetime(2026, 10, d, 9, tzinfo=UTC)


def _snap(
    domain_id: str,
    indicator_id: int,
    periode: str,
    value: Decimal | None,
    first: int,
    last: int,
    **kw: Any,
) -> dict[str, Any]:
    base = kw.pop("base", f"Ind {indicator_id}")
    return {
        "domain_id": domain_id,
        "indicator_id": indicator_id,
        "periode": periode,
        "title": kw.pop("title", f"{base}, {periode}"),
        "var": kw.pop("var", None),
        "subject_csa": 536,
        "name": f"{base} name",
        "value": value,
        "unit": kw.pop("unit", "Persen"),
        "category": 2,
        "data_source": "BPS",
        "first_seen": _day(first),
        "last_seen": _day(last),
        **kw,
    }


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    """0000: indicator 3 (inflation, var 2263 exists) has 3 periodes, the July one revised under
    a second title; 7 has one periode; 10 went down (var 196 not crawled); 14's previous value is
    non-numeric (NULL). 1100 (Aceh) has one indicator; 3100 none; 1101 is a regency."""
    with db_engine.begin() as conn:
        conn.execute(
            insert(domain),
            [
                {"domain_id": "0000", "name": "Indonesia", "level": "pusat"},
                {"domain_id": "1100", "name": "Aceh", "level": "prov"},
                {"domain_id": "3100", "name": "DKI Jakarta", "level": "prov"},
                {"domain_id": "1101", "name": "Simeulue", "level": "kab"},
            ],
        )
        conn.execute(
            insert(variable),
            [
                {"domain_id": "0000", "var_id": 2263, "title": "Inflasi Y-on-Y", "unit": "%"},
                {"domain_id": "1100", "var_id": 2263, "title": "Inflasi Aceh", "unit": "%"},
            ],
        )
        conn.execute(
            insert(indicator_snapshot),
            [
                _snap("0000", 3, "Juli 2026", Decimal("2.90"), 1, 2, base="Inflasi", var=2263,
                      title="Inflasi (old title), Juli 2026"),
                _snap("0000", 3, "Juli 2026", Decimal("2.95"), 2, 3, base="Inflasi", var=2263),
                _snap("0000", 3, "Agustus 2026", Decimal("3.10"), 4, 6, base="Inflasi", var=2263),
                _snap("0000", 3, "September 2026", Decimal("3.28"), 7, 9, base="Inflasi",
                      var=2263),
                _snap("0000", 7, "Triwulan II 2026", Decimal("5.29"), 1, 9, var=104),
                _snap("0000", 10, "Juli 2026", Decimal("26612.2"), 1, 5, var=196,
                      unit="Juta US$"),
                _snap("0000", 10, "Agustus 2026", Decimal("23060"), 6, 9, var=196,
                      unit="Juta US$"),
                _snap("0000", 14, "Semester 2 (September) 2025", None, 1, 4),
                _snap("0000", 14, "Semester 1 (Maret) 2026", Decimal("8.07"), 5, 9),
                _snap("0000", 20, "Agustus 2026", Decimal("0"), 1, 4),
                _snap("0000", 20, "September 2026", Decimal("5"), 5, 9),
                _snap("1100", 3, "September 2026", Decimal("1.5"), 7, 9, base="Inflasi Aceh",
                      var=2263),
            ],
        )  # fmt: skip
    return db_engine


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _by_id(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {i["indicator_id"]: i for i in body["items"]}


def test_indicator_label_strips_the_period_suffix() -> None:
    assert indicator_label("Inflasi Year on Year, September 2026") == "Inflasi Year on Year"
    assert indicator_label("Umur Harapan Hidup Laki-laki (LF SP2020), 2025") == (
        "Umur Harapan Hidup Laki-laki (LF SP2020)"
    )
    assert indicator_label("Pertumbuhan Ekonomi, Triwulan II 2026") == "Pertumbuhan Ekonomi"
    # No year after the last comma → unchanged.
    assert indicator_label("Ekspor, Impor") == "Ekspor, Impor"
    assert indicator_label("Inflasi 2026") == "Inflasi 2026"
    assert indicator_label("") == ""


async def test_latest_per_indicator(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/indicators", domain="0000")
    assert body["domain"] == {
        "domain_id": "0000",
        "name": "Indonesia",
        "url": None,
        "level": "pusat",
    }
    assert [i["indicator_id"] for i in body["items"]] == [3, 7, 10, 14, 20]
    infl = _by_id(body)[3]
    assert infl["title"] == "Inflasi, September 2026"
    assert infl["label"] == "Inflasi"
    assert infl["periode"] == "September 2026"
    assert infl["value"] == 3.28
    assert infl["unit"] == "Persen"
    assert infl["name"] == "Inflasi name"
    assert infl["data_source"] == "BPS"
    assert infl["last_seen"].startswith("2026-10-09")


async def test_domain_defaults_to_national(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/indicators")
    assert body["domain"]["domain_id"] == "0000"
    assert len(body["items"]) == 5


async def test_change_vs_previous_periode(client: httpx.AsyncClient, seeded: Engine) -> None:
    items = _by_id(await _get(client, "/indicators", domain="0000"))
    # Previous = the periode seen just before the latest one (Agustus), not the oldest.
    infl = items[3]
    assert infl["previous"] == {"periode": "Agustus 2026", "value": 3.1}
    assert infl["change"] == pytest.approx(0.18)
    assert infl["change_pct"] == pytest.approx(0.18 / 3.1 * 100)
    # Down.
    assert items[10]["change"] == pytest.approx(23060 - 26612.2)
    assert items[10]["change_pct"] < 0
    # Only one periode → no previous, no change.
    assert items[7]["previous"] is None
    assert items[7]["change"] is None
    assert items[7]["change_pct"] is None
    # Previous value not numeric (NULL) → previous shown, change unknown.
    assert items[14]["previous"] == {"periode": "Semester 2 (September) 2025", "value": None}
    assert items[14]["change"] is None
    # Previous value 0 → absolute change only.
    assert items[20]["change"] == 5
    assert items[20]["change_pct"] is None


async def test_variable_link_only_when_crawled(client: httpx.AsyncClient, seeded: Engine) -> None:
    items = _by_id(await _get(client, "/indicators", domain="0000"))
    assert items[3]["var"] == 2263
    assert items[3]["variable"] == {
        "domain_id": "0000",
        "var_id": 2263,
        "title": "Inflasi Y-on-Y",
    }
    # var 196 / 104 aren't in the variable table; 14 has no var at all.
    assert items[10]["var"] == 196
    assert items[10]["variable"] is None
    assert items[7]["variable"] is None
    assert items[14]["var"] is None
    assert items[14]["variable"] is None


async def test_province_domain(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/indicators", domain="1100")
    assert body["domain"]["name"] == "Aceh"
    [item] = body["items"]
    assert item["value"] == 1.5
    # The variable is looked up in the indicator's own domain.
    assert item["variable"] == {"domain_id": "1100", "var_id": 2263, "title": "Inflasi Aceh"}
    # A known domain without indicators (yet) → empty list, not 404.
    body = await _get(client, "/indicators", domain="3100")
    assert body["items"] == []


async def test_unknown_domain(client: httpx.AsyncClient, seeded: Engine) -> None:
    r = await client.get("/indicators", params={"domain": "9900"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Domain not found"}
    r = await client.get("/indicators", params={"domain": "abc"})
    assert r.status_code == 422


async def test_history_is_chronological(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/indicators/0000/3/history")
    assert body["domain_id"] == "0000"
    assert body["indicator_id"] == 3
    assert body["label"] == "Inflasi"
    assert body["title"] == "Inflasi, September 2026"
    assert body["unit"] == "Persen"
    assert body["variable"]["var_id"] == 2263
    # One point per periode (the latest-seen row of a revised periode), oldest first.
    points = [(p["periode"], p["value"]) for p in body["points"]]
    assert points == [("Juli 2026", 2.95), ("Agustus 2026", 3.1), ("September 2026", 3.28)]
    first = body["points"][0]
    assert first["title"] == "Inflasi, Juli 2026"
    assert first["first_seen"].startswith("2026-10-01")  # earliest sighting of the periode
    assert first["last_seen"].startswith("2026-10-03")


async def test_history_with_null_value(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _get(client, "/indicators/0000/14/history")
    assert [p["value"] for p in body["points"]] == [None, 8.07]
    assert body["variable"] is None


async def test_history_unknown(client: httpx.AsyncClient, seeded: Engine) -> None:
    r = await client.get("/indicators/0000/999/history")
    assert r.status_code == 404
    assert r.json() == {"detail": "Indicator not found"}
    r = await client.get("/indicators/3100/3/history")
    assert r.status_code == 404
    r = await client.get("/indicators/12/3/history")
    assert r.status_code == 422


def test_openapi_indicator_operations() -> None:
    schema = openapi_schema()
    listing = schema["paths"]["/indicators"]["get"]
    assert listing["operationId"] == "listIndicators"
    assert listing["tags"] == ["indicators"]
    assert {p["name"] for p in listing["parameters"]} == {"domain"}
    history = schema["paths"]["/indicators/{domain}/{indicator_id}/history"]["get"]
    assert history["operationId"] == "getIndicatorHistory"
    assert history["tags"] == ["indicators"]
    assert {"IndicatorList", "Indicator", "IndicatorHistory", "IndicatorPoint"} <= schema[
        "components"
    ]["schemas"].keys()
