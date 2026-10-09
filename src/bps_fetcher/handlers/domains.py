"""``domains`` task: fetch ``/domain``, upsert the ``domain`` table, fan out ``var_list`` tasks.

Task params (all optional):

- ``type`` — ``/domain`` type (default ``all``; ``prov``, ``kab``, ``kabbyprov``…), with ``prov``
  passed through for ``kabbyprov``.
- ``level`` — a level or list of levels (``pusat``/``prov``/``kab``); only domains at those levels
  get a ``var_list`` child. Every fetched domain is stored regardless.

Level comes from the id: ``0000`` → pusat, ``xx00`` → prov, anything else → kab.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from bps_fetcher.client import BpsApiError
from bps_fetcher.db.schema import DOMAIN_LEVELS, domain
from bps_fetcher.paginate import NOT_AVAILABLE
from bps_fetcher.worker import Child, HandlerResult, RawResponse, TaskContext, register

KIND = "domains"
CHILD_KIND = "var_list"
_API_PARAMS = ("type", "prov")
_ID_RE = re.compile(r"\d{4}")


@dataclass(frozen=True, slots=True)
class DomainRow:
    domain_id: str
    name: str
    url: str | None
    level: str


def domain_level(domain_id: str) -> str:
    if not _ID_RE.fullmatch(domain_id):
        raise ValueError(f"malformed domain_id {domain_id!r} (expected 4 digits)")
    if domain_id == "0000":
        return "pusat"
    if domain_id.endswith("00"):
        return "prov"
    return "kab"


def parse_domains(body: Mapping[str, Any]) -> list[DomainRow]:
    """Rows from a ``/domain`` response; ``[]`` when the API reports not-available."""
    if body.get("data-availability") == NOT_AVAILABLE:
        return []
    data = body.get("data")
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        raise BpsApiError("unexpected /domain response shape")
    rows = []
    for item in data[1]:
        domain_id = str(item["domain_id"])
        rows.append(
            DomainRow(
                domain_id=domain_id,
                name=str(item["domain_name"]),
                url=item.get("domain_url") or None,
                level=domain_level(domain_id),
            )
        )
    return rows


def upsert_domains(conn: Connection, rows: Iterable[DomainRow]) -> None:
    """Insert or update (only rows whose name/url/level changed)."""
    values = [
        {"domain_id": r.domain_id, "name": r.name, "url": r.url, "level": r.level} for r in rows
    ]
    if not values:
        return
    stmt = insert(domain).values(values)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[domain.c.domain_id],
        set_={"name": ex.name, "url": ex.url, "level": ex.level},
        where=(domain.c.name.is_distinct_from(ex.name))
        | (domain.c.url.is_distinct_from(ex.url))
        | (domain.c.level.is_distinct_from(ex.level)),
    )
    conn.execute(stmt)


def _levels(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset(DOMAIN_LEVELS)
    levels = frozenset([value] if isinstance(value, str) else value)
    unknown = levels - set(DOMAIN_LEVELS)
    if unknown or not levels:
        raise ValueError(f"unknown level filter {sorted(unknown)!r}; use {DOMAIN_LEVELS}")
    return levels


@register(KIND)
async def domains(ctx: TaskContext) -> HandlerResult:
    levels = _levels(ctx.params.get("level"))
    api_params = {k: ctx.params[k] for k in _API_PARAMS if k in ctx.params}
    api_params.setdefault("type", "all")
    body = await ctx.client.get("domain", **api_params)
    rows = parse_domains(body)
    upsert_domains(ctx.conn, rows)
    return HandlerResult(
        raw=[RawResponse("domain", api_params, body)],
        children=[Child(CHILD_KIND, {"domain": r.domain_id}) for r in rows if r.level in levels],
    )
