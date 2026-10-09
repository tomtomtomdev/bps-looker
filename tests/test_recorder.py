import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from bps_fetcher.client import BASE_URL
from bps_fetcher.recorder import FIXTURES, FixtureSpec, record

KEY = "s3cretFakeKey987"
UA = "Mozilla/5.0 (test) bps-looker"


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_writes_named_fixture_without_key(tmp_path: Path, http: httpx.AsyncClient) -> None:
    body = {
        "status": "OK",
        "data-availability": "available",
        # A body echoing the key (in a URL and verbatim) must be scrubbed too.
        "data": [{"page": 1}, [{"url": f"https://x/?key={KEY}", "echo": f"k {KEY} k"}]],
    }
    route = respx.get(f"{BASE_URL}list").mock(return_value=httpx.Response(200, json=body))
    spec = FixtureSpec("var_0000_p1", "list", {"model": "var", "domain": "0000", "page": 1})

    path = await record(http, spec, api_key=KEY, user_agent=UA, out_dir=tmp_path)

    assert path == tmp_path / "var_0000_p1.json"
    raw = path.read_text()
    assert KEY not in raw
    fixture = json.loads(raw)
    assert fixture["name"] == "var_0000_p1"
    assert fixture["request"]["path"] == "list"
    assert fixture["request"]["params"] == {"model": "var", "domain": "0000", "page": "1"}
    assert "key" not in fixture["request"]["params"]
    assert "key=***" in fixture["request"]["url"]
    assert fixture["status_code"] == 200
    assert fixture["body"]["data"][1][0] == {"url": "https://x/?key=***", "echo": "k *** k"}
    request = route.calls.last.request
    assert request.url.params["key"] == KEY
    assert request.headers["User-Agent"] == UA


@respx.mock
async def test_records_error_bodies_and_non_json(tmp_path: Path, http: httpx.AsyncClient) -> None:
    err = {"status": "Error", "message": "bad"}
    respx.get(f"{BASE_URL}list").mock(return_value=httpx.Response(200, json=err))
    respx.get(f"{BASE_URL}domain").mock(
        return_value=httpx.Response(403, text=f"<html>blocked {KEY}</html>")
    )

    p1 = await record(
        http,
        FixtureSpec("e", "list", {"model": "data"}),
        api_key=KEY,
        user_agent=UA,
        out_dir=tmp_path,
    )
    p2 = await record(
        http, FixtureSpec("w", "domain", {}), api_key=KEY, user_agent=UA, out_dir=tmp_path
    )

    assert json.loads(p1.read_text())["body"] == err
    waf = json.loads(p2.read_text())
    assert waf["status_code"] == 403
    assert waf["body"] == "<html>blocked ***</html>"


@respx.mock
async def test_scrubs_extra_secrets(tmp_path: Path, http: httpx.AsyncClient) -> None:
    """The bad-key sample is sent with a fake key, but the real one must still never land."""
    respx.get(f"{BASE_URL}list").mock(
        return_value=httpx.Response(200, json={"message": f"real {KEY}"})
    )
    path = await record(
        http,
        FixtureSpec("error_bad_key", "list", {"model": "subcat"}),
        api_key="not-a-real-key",
        user_agent=UA,
        out_dir=tmp_path,
        secrets=[KEY],
    )
    assert KEY not in path.read_text()


def test_fixture_catalog_names_unique_and_complete() -> None:
    names = [spec.name for spec in FIXTURES]
    assert len(names) == len(set(names))
    for required in (
        "domain_all",
        "var_0000_p1",
        "th_0000_1804",
        "data_0000_1804",
        "data_0000_2263",
        "indicators_0000_p1",
        "indicators_0000_p2",
        "trade_exp_annual_03_2024",
        "trade_exp_monthly_03_2024",
        "error_bad_key",
        "error_data_missing_th",
        "error_data_too_many_th",
    ):
        assert required in names
