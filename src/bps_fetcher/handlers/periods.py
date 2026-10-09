"""``th_list`` task: page through ``/list?model=th`` for one variable, upsert ``period``, fan out
one ``data`` task per window of at most :func:`max_th_per_call` contiguous periods.

Task params: ``domain`` (4-digit id) and ``var`` (int); the ``variable`` row must already exist
(run ``var_list`` first). Every page's response is kept as a raw response.

``model=data`` accepts at most 3 periods per call on the national domain and only 2 on province
and regency domains (``The maximum allowed number of years for the 'th' parameter is 2``, found in
S19; the ``data`` handler also splits a window that hits this error). Windows are runs of
consecutive ``th_id``s chunked from the lowest id, so each ``data`` task's ``th`` is a single id
(``117``) or a range (``117:119``). Chunking from the low end keeps existing windows stable when
newer periods appear: only the last window changes.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, and_, exists, select
from sqlalchemy.dialects.postgresql import insert

from bps_fetcher.db.schema import period, variable
from bps_fetcher.handlers.domains import domain_level
from bps_fetcher.paginate import is_not_available, items_of, paginate_pages
from bps_fetcher.worker import Child, HandlerResult, RawResponse, TaskContext, register

KIND = "th_list"
CHILD_KIND = "data"
MODEL = "th"
MAX_TH_PER_CALL = 3  # national (pusat)
MAX_TH_PER_CALL_REGIONAL = 2  # province + regency domains (verified 2026-10-09)


def max_th_per_call(domain_id: str) -> int:
    """Most periods one ``model=data`` call accepts on this domain."""
    return MAX_TH_PER_CALL if domain_level(domain_id) == "pusat" else MAX_TH_PER_CALL_REGIONAL


class ThItem(BaseModel):
    """One item of ``/list?model=th``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    th_id: int
    label: str = Field(alias="th")


def windows(th_ids: Iterable[int], size: int = MAX_TH_PER_CALL) -> list[list[int]]:
    """Sorted, de-duplicated ``th_ids`` cut into runs of consecutive ids of at most ``size``.

    A gap always starts a new window, so every window is a contiguous range.
    """
    if size < 1:
        raise ValueError("window size must be >= 1")
    out: list[list[int]] = []
    for th in sorted(set(th_ids)):
        if out and th == out[-1][-1] + 1 and len(out[-1]) < size:
            out[-1].append(th)
        else:
            out.append([th])
    return out


def th_param(window: Sequence[int]) -> str:
    """The ``th`` param for a contiguous window: ``"117"`` or ``"117:119"``."""
    if not window:
        raise ValueError("empty th window")
    first, last = window[0], window[-1]
    if list(window) != list(range(first, last + 1)):
        raise ValueError(f"th window {list(window)!r} is not contiguous")
    return str(first) if first == last else f"{first}:{last}"


def upsert_periods(conn: Connection, domain_id: str, var_id: int, items: Iterable[ThItem]) -> None:
    """Insert or update labels (unchanged rows skipped)."""
    values = [
        {"domain_id": domain_id, "var_id": var_id, "th_id": th_id, "label": label}
        for th_id, label in {i.th_id: i.label for i in items}.items()
    ]
    if not values:
        return
    stmt = insert(period).values(values)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[period.c.domain_id, period.c.var_id, period.c.th_id],
        set_={"label": ex.label},
        where=period.c.label.is_distinct_from(ex.label),
    )
    conn.execute(stmt)


def _params(params: dict[str, Any]) -> tuple[str, int]:
    domain_id = params.get("domain")
    if not isinstance(domain_id, str) or not domain_id:
        raise ValueError(f"th_list needs a 'domain' param (got {domain_id!r})")
    var_id = params.get("var")
    if not isinstance(var_id, int) or isinstance(var_id, bool):
        raise ValueError(f"th_list needs an int 'var' param (got {var_id!r})")
    return domain_id, var_id


@register(KIND)
async def th_list(ctx: TaskContext) -> HandlerResult:
    domain_id, var_id = _params(ctx.params)
    known = exists().where(and_(variable.c.domain_id == domain_id, variable.c.var_id == var_id))
    if not ctx.conn.execute(select(known)).scalar():
        raise LookupError(f"variable {domain_id}/{var_id} not in the variable table; run var_list")

    result = HandlerResult()
    items: list[ThItem] = []
    async for request, body in paginate_pages(ctx.client, MODEL, domain=domain_id, var=var_id):
        result.raw.append(RawResponse("list", request, body))
        if is_not_available(body):
            break
        items.extend(ThItem.model_validate(i) for i in items_of(body, MODEL)[1])

    upsert_periods(ctx.conn, domain_id, var_id, items)
    result.children = [
        Child(CHILD_KIND, {"domain": domain_id, "var": var_id, "th": th_param(w)})
        for w in windows((i.th_id for i in items), max_th_per_call(domain_id))
    ]
    return result
