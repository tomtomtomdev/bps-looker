import logging
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from tenacity import wait_none

from bps_fetcher.client import (
    BASE_URL,
    BpsApiError,
    BpsAuthError,
    BpsClient,
    BpsNullResponseError,
    BpsTransientError,
)

KEY = "s3cretFakeKey987"
UA = "Mozilla/5.0 (test) bps-looker"
OK_BODY = {"status": "OK", "data-availability": "available", "data": []}
AUTH_ERROR = {
    "status": "Error",
    "message": "You are not Allowed to take this action. Please re-check your key",
}
WAF_HTML = "<html><head><title>Perimeter WAF Block</title></head><body>blocked</body></html>"


@pytest.fixture
async def client() -> AsyncIterator[BpsClient]:
    async with BpsClient(
        api_key=KEY, user_agent=UA, rps=1000, max_attempts=3, wait=wait_none()
    ) as c:
        yield c


def test_base_url() -> None:
    assert BASE_URL == "https://webapi.bps.go.id/v1/api/"


@respx.mock
async def test_sends_user_agent_key_and_params(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}list").mock(return_value=httpx.Response(200, json=OK_BODY))

    body = await client.get("list", model="var", domain="0000", page=2)

    assert body == OK_BODY
    request = route.calls.last.request
    assert request.headers["User-Agent"] == UA
    assert request.url.params["key"] == KEY
    assert request.url.params["model"] == "var"
    assert request.url.params["domain"] == "0000"
    assert request.url.params["page"] == "2"
    assert request.url.path == "/v1/api/list"


@respx.mock
async def test_leading_slash_and_trailing_path(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}dataexim/").mock(return_value=httpx.Response(200, json=OK_BODY))
    await client.get("/dataexim/", tahun=2024)
    assert route.called
    assert route.calls.last.request.url.params["tahun"] == "2024"


@respx.mock
async def test_status_error_raises_api_error_not_retried(client: BpsClient) -> None:
    message = "'th' parameter is required and must be an integer"
    route = respx.get(f"{BASE_URL}list").mock(
        return_value=httpx.Response(200, json={"status": "Error", "message": message})
    )
    with pytest.raises(BpsApiError) as info:
        await client.get("list", model="data", domain="0000", var=1804)
    assert info.value.message == message
    assert message in str(info.value)
    assert not isinstance(info.value, BpsAuthError)
    assert route.call_count == 1


@respx.mock
async def test_key_error_raises_auth_error_not_retried(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}list").mock(return_value=httpx.Response(200, json=AUTH_ERROR))
    with pytest.raises(BpsAuthError):
        await client.get("list", model="var")
    assert route.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text=WAF_HTML, headers={"Content-Type": "text/html"}),
        httpx.Response(403, text=WAF_HTML, headers={"Content-Type": "text/html"}),
        httpx.Response(500, json={"status": "Error"}),
        httpx.Response(503, text="Service Unavailable"),
    ],
    ids=["waf-200", "waf-403", "500", "503"],
)
@respx.mock
async def test_transient_responses_retried_then_raise(
    client: BpsClient, response: httpx.Response
) -> None:
    route = respx.get(f"{BASE_URL}list").mock(return_value=response)
    with pytest.raises(BpsTransientError):
        await client.get("list", model="var")
    assert route.call_count == 3


@respx.mock
async def test_timeout_retried_then_raise(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}list").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(BpsTransientError):
        await client.get("list", model="var")
    assert route.call_count == 3


@respx.mock
async def test_transient_then_success(client: BpsClient) -> None:
    route = respx.get(f"{BASE_URL}list").mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.ConnectTimeout("slow"),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    assert await client.get("list", model="var") == OK_BODY
    assert route.call_count == 3


@respx.mock
async def test_non_json_object_raises_api_error(client: BpsClient) -> None:
    respx.get(f"{BASE_URL}list").mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(BpsApiError):
        await client.get("list", model="var")


@respx.mock
async def test_json_null_raises_null_response_error_not_retried(client: BpsClient) -> None:
    # Seen live (S13): too-large data windows answer HTTP 200 with the JSON literal ``null``.
    route = respx.get(f"{BASE_URL}list").mock(
        return_value=httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(BpsNullResponseError, match="null"):
        await client.get("list", model="data", domain="0000", var=2096, th="118:120")
    assert route.call_count == 1
    assert issubclass(BpsNullResponseError, BpsApiError)


@respx.mock
async def test_rate_limiter_caps_requests_per_second() -> None:
    import asyncio

    rps = 20.0
    n = 10
    stamps: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        return httpx.Response(200, json=OK_BODY)

    respx.get(f"{BASE_URL}list").mock(side_effect=record)
    async with BpsClient(api_key=KEY, user_agent=UA, rps=rps, wait=wait_none()) as c:
        await asyncio.gather(*(c.get("list", page=i) for i in range(n)))

    assert len(stamps) == n
    # Strict spacing: n requests need at least (n - 1) / rps seconds.
    assert stamps[-1] - stamps[0] >= (n - 1) / rps * 0.9


@respx.mock
async def test_errors_and_logs_never_contain_key(
    client: BpsClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    respx.get(f"{BASE_URL}list").mock(
        side_effect=httpx.ConnectError(f"boom at {BASE_URL}list?key={KEY}")
    )
    respx.get(f"{BASE_URL}view").mock(
        return_value=httpx.Response(200, json={"status": "Error", "message": f"bad key {KEY}"})
    )

    with pytest.raises(BpsTransientError) as transient:
        await client.get("list", model="var")
    with pytest.raises(BpsApiError) as api:
        await client.get("view", model="var")

    for exc in (transient.value, api.value):
        assert KEY not in str(exc)
        assert KEY not in repr(exc)
        assert exc.__cause__ is None or KEY not in str(exc.__cause__)
        assert exc.__suppress_context__ or exc.__context__ is None
    assert KEY not in caplog.text
    assert "retry" in caplog.text.lower()


def test_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from bps_fetcher.settings import Settings

    monkeypatch.setenv("BPS_API_KEY", KEY)
    monkeypatch.setenv("BPS_RPS", "3.0")
    monkeypatch.setenv("BPS_USER_AGENT", UA)
    settings = Settings(_env_file=None)
    c = BpsClient.from_settings(settings)
    assert c.user_agent == UA
    assert c.rps == 3.0
    assert KEY not in repr(c)
