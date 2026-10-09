"""Iterate the items of a paginated BPS list endpoint."""

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Protocol

from bps_fetcher.client import BpsApiError

NOT_AVAILABLE = "not-available"
# "Nothing here" markers in ``data-availability``; ``list-not-available`` (with ``data: ""``) was
# seen live for empty ``model=data`` windows (S13 national crawl).
NOT_AVAILABLE_VALUES = frozenset({NOT_AVAILABLE, "list-not-available"})


def is_not_available(body: Any) -> bool:
    """True when a response says there is no data (any :data:`NOT_AVAILABLE_VALUES` marker)."""
    return isinstance(body, dict) and body.get("data-availability") in NOT_AVAILABLE_VALUES


# Models served by their own path instead of ``/list?model=...``; not paginated.
_OWN_PATH_MODELS = frozenset({"domain"})


class _Getter(Protocol):
    async def get(self, path: str, **params: Any) -> dict[str, Any]: ...


async def paginate(client: _Getter, model: str, **params: Any) -> AsyncIterator[dict[str, Any]]:
    """Yield every item of ``model`` across pages ``page=start..pages``.

    ``data[0]`` holds the page meta and ``data[1]`` the items. Stops when the API reports
    ``data-availability: not-available`` or when the meta has no ``pages``. ``/domain`` is one
    unpaginated call (real meta: ``{"page": 1, "pages": 1, "total": 549}``).
    """
    if model in _OWN_PATH_MODELS:
        body = await client.get(model, **params)
        if not is_not_available(body):
            for item in items_of(body, model)[1]:
                yield item
        return

    async for _, body in paginate_pages(client, model, **params):
        if is_not_available(body):
            return
        for item in items_of(body, model)[1]:
            yield item


async def paginate_pages(
    client: _Getter, model: str, **params: Any
) -> AsyncGenerator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(request_params, body)`` for every page of ``/list?model=…`` — for callers that
    keep raw responses. The last body may be a ``not-available`` one; malformed bodies raise.
    """
    page = int(params.pop("page", 1))
    while True:
        request = {"model": model, **params, "page": page}
        body = await client.get("list", **request)
        yield request, body
        if is_not_available(body):
            return
        meta, _ = items_of(body, model)
        pages = meta.get("pages")
        if pages is None or page >= int(pages):
            return
        page += 1


def items_of(body: dict[str, Any], model: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = body.get("data")
    if (
        not isinstance(data, list)
        or len(data) < 2
        or not isinstance(data[0], dict)
        or not isinstance(data[1], list)
    ):
        raise BpsApiError(f"unexpected list response shape for model={model!r}")
    return data[0], data[1]
