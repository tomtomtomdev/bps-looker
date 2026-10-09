"""Check earlier assumptions against recorded live responses (tests/fixtures/)."""

import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from bps_fetcher.client import BASE_URL, BpsApiError, BpsAuthError, BpsClient, BpsTransientError
from bps_fetcher.paginate import paginate
from bps_fetcher.recorder import FIXTURES
from conftest import FIXTURES_DIR, read_fixture

Body = Callable[[str], Any]
KEY = "s3cretFakeKey987"


class FakeClient:
    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        return self.bodies.pop(0)


def test_every_catalog_fixture_is_recorded_and_key_free() -> None:
    for spec in FIXTURES:
        path = FIXTURES_DIR / f"{spec.name}.json"
        assert path.exists(), spec.name
        text = path.read_text(encoding="utf-8")
        assert path.stat().st_size < 1_000_000, spec.name
        # The only key ever stored is the redaction marker.
        assert set(re.findall(r"key=([^&\"\s]*)", text)) <= {"***"}, spec.name


async def test_domain_all_shape_and_paginate(fixture_body: Body) -> None:
    body = fixture_body("domain_all")
    # Real /domain meta has pages (unlike the S3 assumption) but no per_page/count.
    assert body["data"][0] == {"page": 1, "pages": 1, "total": 549}
    client = FakeClient([body])

    items = [item async for item in paginate(client, "domain", type="all")]

    assert len(items) == 549
    assert set(items[0]) == {"domain_id", "domain_name", "domain_url"}
    assert items[0]["domain_id"] == "0000"
    assert client.calls == [("domain", {"type": "all"})]


async def test_var_list_page_meta(fixture_body: Body) -> None:
    body = fixture_body("var_0000_p1")
    meta, items = body["data"]
    assert meta["per_page"] == 10
    assert meta["pages"] == -(-meta["total"] // 10)
    assert len(items) == 10
    assert {"var_id", "title", "sub_id", "subcsa_id", "notes", "unit", "vertical"} <= set(items[0])
    # notes arrive HTML-escaped
    assert any("&lt;" in item["notes"] for item in items)


async def test_indicators_paginate_two_pages(fixture_body: Body) -> None:
    client = FakeClient([fixture_body("indicators_0000_p1"), fixture_body("indicators_0000_p2")])

    items = [item async for item in paginate(client, "indicators", domain="0000")]

    assert len(items) == 16
    assert [c[1]["page"] for c in client.calls] == [1, 2]
    assert {"var", "indicator_id", "title", "value", "unit", "periode"} <= set(items[0])


def test_th_list(fixture_body: Body) -> None:
    meta, items = fixture_body("th_0000_1804")["data"]
    assert meta["pages"] == 1
    assert {"th_id": 117, "th": "2017"} in items


def _keys(body: dict[str, Any]) -> set[str]:
    var = body["var"][0]["val"]
    return {
        f"{vv['val']}{var}{tv['val']}{th['val']}{tt['val']}"
        for vv in body["vervar"]
        for tv in body["turvar"]
        for th in body["tahun"]
        for tt in body["turtahun"]
    }


def test_data_annual_keys_are_concatenation(fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    assert body["last_update"] == "2020-03-26 00:00:00"
    assert [t["val"] for t in body["tahun"]] == [117, 118, 119]
    assert body["datacontent"]["1180401170"] == 440
    assert set(body["datacontent"]) == _keys(body)


def test_data_monthly_has_turtahun_1_to_13_but_values_for_months_only(
    fixture_body: Body,
) -> None:
    body = fixture_body("data_0000_2263")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", body["last_update"])
    assert [t["val"] for t in body["turtahun"]] == list(range(1, 14))
    assert body["turtahun"][12]["label"] == "Tahunan"
    assert len(body["vervar"]) == 39
    assert len(body["datacontent"]) == 39 * 12
    assert set(body["datacontent"]) <= _keys(body)
    assert not any(k.endswith("12413") for k in body["datacontent"])


def test_trade_annual_and_monthly(fixture_body: Body) -> None:
    annual = fixture_body("trade_exp_annual_03_2024")
    monthly = fixture_body("trade_exp_monthly_03_2024")
    for body in (annual, monthly):
        assert body["status"] == "OK"
        assert isinstance(body["data"], list)  # flat rows, no pagination meta
        assert set(body["metadata"]) >= {"value", "netweight", "kodehs", "pod", "ctr", "tahun"}
        assert all(row["kodehs"].startswith("[03] ") for row in body["data"])
    assert "bulan" not in annual["data"][0]
    assert all(re.fullmatch(r"\[(0[1-9]|1[0-2])\] \w+", row["bulan"]) for row in monthly["data"])
    assert len({row["bulan"][:4] for row in monthly["data"]}) == 12


@pytest.fixture
async def client() -> Any:
    async with BpsClient(
        api_key=KEY, user_agent="UA", rps=1000, max_attempts=2, wait=wait_none()
    ) as c:
        yield c


def _replay(name: str) -> httpx.Response:
    fx = read_fixture(name)
    body = fx["body"]
    if isinstance(body, str):
        return httpx.Response(fx["status_code"], text=body, headers={"content-type": "text/html"})
    return httpx.Response(fx["status_code"], json=body)


@respx.mock
async def test_real_bad_key_message_is_auth_error(client: BpsClient) -> None:
    respx.get(f"{BASE_URL}list").mock(return_value=_replay("error_bad_key"))
    with pytest.raises(BpsAuthError, match="re-check your key"):
        await client.get("list", model="subcat", domain="0000")


@respx.mock
async def test_real_waf_block_is_transient(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}list").mock(return_value=_replay("error_waf_block"))
    with pytest.raises(BpsTransientError, match="403"):
        await client.get("list", model="subcat", domain="0000")
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("error_data_missing_th", "'th' parameter is required"),
        ("error_data_too_many_th", "maximum allowed number of years for the 'th' parameter is 3"),
    ],
)
async def test_real_data_errors_are_api_errors(client: BpsClient, name: str, message: str) -> None:
    respx.get(f"{BASE_URL}list").mock(return_value=_replay(name))
    with pytest.raises(BpsApiError, match=re.escape(message)) as exc:
        await client.get("list", model="data", domain="0000", var=1804)
    assert not isinstance(exc.value, BpsAuthError)
