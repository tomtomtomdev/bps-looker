from typing import Any

import pytest

from bps_fetcher.client import BpsApiError
from bps_fetcher.paginate import paginate


class FakeClient:
    """Records calls and answers from a list of canned bodies (one per call)."""

    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        if not self.bodies:
            raise AssertionError(f"unexpected extra call: {path} {params}")
        return self.bodies.pop(0)


def page(n: int, pages: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "OK",
        "data-availability": "available",
        "data": [
            {"page": n, "pages": pages, "per_page": 10, "count": len(items), "total": 99},
            items,
        ],
    }


NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}


async def collect(client: FakeClient, model: str, **params: Any) -> list[dict[str, Any]]:
    return [item async for item in paginate(client, model, **params)]


async def test_iterates_pages_and_yields_items() -> None:
    client = FakeClient(
        [
            page(1, 3, [{"var_id": 1}, {"var_id": 2}]),
            page(2, 3, [{"var_id": 3}]),
            page(3, 3, [{"var_id": 4}]),
        ]
    )

    items = await collect(client, "var", domain="0000")

    assert items == [{"var_id": 1}, {"var_id": 2}, {"var_id": 3}, {"var_id": 4}]
    assert client.calls == [
        ("list", {"model": "var", "domain": "0000", "page": 1}),
        ("list", {"model": "var", "domain": "0000", "page": 2}),
        ("list", {"model": "var", "domain": "0000", "page": 3}),
    ]


async def test_single_page() -> None:
    client = FakeClient([page(1, 1, [{"th_id": 117}])])
    assert await collect(client, "th", domain="0000", var=1804) == [{"th_id": 117}]
    assert len(client.calls) == 1


async def test_stops_on_not_available_first_page() -> None:
    client = FakeClient([NOT_AVAILABLE])
    assert await collect(client, "var", domain="9999") == []
    assert len(client.calls) == 1


async def test_stops_on_not_available_mid_way() -> None:
    client = FakeClient([page(1, 5, [{"id": 1}]), NOT_AVAILABLE])
    assert await collect(client, "var", domain="0000") == [{"id": 1}]
    assert len(client.calls) == 2


async def test_pages_missing_domain_endpoint() -> None:
    body = {
        "status": "OK",
        "data-availability": "available",
        "data": [
            {"page": 1, "per_page": 549, "count": 549, "total": 549},
            [{"domain_id": "0000"}, {"domain_id": "1100"}],
        ],
    }
    client = FakeClient([body])

    items = await collect(client, "domain", type="all")

    assert items == [{"domain_id": "0000"}, {"domain_id": "1100"}]
    assert client.calls == [("domain", {"type": "all"})]


async def test_pages_missing_on_list_model_fetches_once() -> None:
    body = {"status": "OK", "data-availability": "available", "data": [{}, [{"x": 1}]]}
    client = FakeClient([body])
    assert await collect(client, "subcat", domain="0000") == [{"x": 1}]
    assert len(client.calls) == 1


async def test_start_page() -> None:
    client = FakeClient([page(2, 2, [{"id": 2}])])
    assert await collect(client, "var", domain="0000", page=2) == [{"id": 2}]
    assert client.calls == [("list", {"model": "var", "domain": "0000", "page": 2})]


async def test_malformed_data_raises() -> None:
    client = FakeClient([{"status": "OK", "data-availability": "available", "data": "nope"}])
    with pytest.raises(BpsApiError):
        await collect(client, "var", domain="0000")
