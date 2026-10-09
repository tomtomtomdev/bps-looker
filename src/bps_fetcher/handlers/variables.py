"""``var_list`` task: page through ``/list?model=var`` for one domain, upsert ``variable``,
fan out one ``th_list`` task per variable.

Task params: ``domain`` (required, 4-digit id; the ``domain`` row must already exist — run the
``domains`` task first). Every page's response is kept as a raw response.

Items are validated with :class:`VarItem` (unknown fields ignored). The API HTML-escapes
``notes`` (``&lt;p&gt;…``) and sometimes ``def``; both are unescaped, and empty strings become
``NULL``. ``decimal``/``last_update`` are left alone here (the data loader owns them).
"""

import html
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, exists, select
from sqlalchemy.dialects.postgresql import insert

from bps_fetcher.db.schema import domain, variable
from bps_fetcher.paginate import NOT_AVAILABLE, items_of, paginate_pages
from bps_fetcher.worker import Child, HandlerResult, RawResponse, TaskContext, register

KIND = "var_list"
CHILD_KIND = "th_list"
MODEL = "var"

# Columns this handler owns (everything but the keys, ``decimal`` and ``last_update``).
_COLUMNS = (
    "title",
    "unit",
    "sub_id",
    "sub_name",
    "subcsa_id",
    "subcsa_name",
    "def",
    "notes",
    "vertical",
)


class VarItem(BaseModel):
    """One item of ``/list?model=var``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    var_id: int
    title: str
    unit: str | None = None
    sub_id: int | None = None
    sub_name: str | None = None
    subcsa_id: int | None = None
    subcsa_name: str | None = None
    def_: str | None = Field(default=None, alias="def")
    notes: str | None = None
    vertical: int | None = None

    @field_validator("def_", "notes", mode="before")
    @classmethod
    def _unescape(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = html.unescape(v)
            return v or None
        return v

    @field_validator("unit", "sub_name", "subcsa_name", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return v or None

    def row(self, domain_id: str) -> dict[str, Any]:
        return {
            "domain_id": domain_id,
            "var_id": self.var_id,
            "title": self.title,
            "unit": self.unit,
            "sub_id": self.sub_id,
            "sub_name": self.sub_name,
            "subcsa_id": self.subcsa_id,
            "subcsa_name": self.subcsa_name,
            "def": self.def_,
            "notes": self.notes,
            "vertical": self.vertical,
        }


def upsert_variables(conn: Connection, domain_id: str, items: Iterable[VarItem]) -> None:
    """Insert or update the list-owned columns (rows whose values didn't change are skipped)."""
    values = list({i.var_id: i.row(domain_id) for i in items}.values())
    if not values:
        return
    stmt = insert(variable).values(values)
    ex = stmt.excluded
    changed = None
    for c in _COLUMNS:
        cond = variable.c[c].is_distinct_from(ex[c])
        changed = cond if changed is None else changed | cond
    stmt = stmt.on_conflict_do_update(
        index_elements=[variable.c.domain_id, variable.c.var_id],
        set_={c: ex[c] for c in _COLUMNS},
        where=changed,
    )
    conn.execute(stmt)


def _domain_id(params: dict[str, Any]) -> str:
    value = params.get("domain")
    if not isinstance(value, str) or not value:
        raise ValueError(f"var_list needs a 'domain' param (got {value!r})")
    return value


@register(KIND)
async def var_list(ctx: TaskContext) -> HandlerResult:
    domain_id = _domain_id(ctx.params)
    if not ctx.conn.execute(select(exists().where(domain.c.domain_id == domain_id))).scalar():
        raise LookupError(f"domain {domain_id!r} not in the domain table; run 'domains' first")

    result = HandlerResult()
    items: list[VarItem] = []
    async for request, body in paginate_pages(ctx.client, MODEL, domain=domain_id):
        result.raw.append(RawResponse("list", request, body))
        if body.get("data-availability") == NOT_AVAILABLE:
            break
        items.extend(VarItem.model_validate(i) for i in items_of(body, MODEL)[1])

    upsert_variables(ctx.conn, domain_id, items)
    seen: set[int] = set()
    for item in items:
        if item.var_id not in seen:
            seen.add(item.var_id)
            result.children.append(Child(CHILD_KIND, {"domain": domain_id, "var": item.var_id}))
    return result
