"""Iterate the items of a paginated BPS list endpoint."""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from bps_fetcher.client import BpsApiError

NOT_AVAILABLE = "not-available"

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
        if body.get("data-availability") != NOT_AVAILABLE:
            for item in _items(body, model)[1]:
                yield item
        return

    page = int(params.pop("page", 1))
    while True:
        body = await client.get("list", model=model, **params, page=page)
        if body.get("data-availability") == NOT_AVAILABLE:
            return
        meta, items = _items(body, model)
        for item in items:
            yield item
        pages = meta.get("pages")
        if pages is None or page >= int(pages):
            return
        page += 1


def _items(body: dict[str, Any], model: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = body.get("data")
    if (
        not isinstance(data, list)
        or len(data) < 2
        or not isinstance(data[0], dict)
        or not isinstance(data[1], list)
    ):
        raise BpsApiError(f"unexpected list response shape for model={model!r}")
    return data[0], data[1]
