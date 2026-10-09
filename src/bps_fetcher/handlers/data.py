"""``data`` task: fetch ``/list?model=data`` for one variable and ``th`` window, load it.

Task params (emitted by ``th_list``): ``domain`` (4-digit id), ``var`` (int), ``th`` (``"117"`` or
``"117:119"``, at most 3 periods). The ``variable`` row must already exist. The response is kept
as a raw response, parsed with :func:`~bps_fetcher.parse_data.parse_data` and upserted into
``observation``, ``dim_vervar``/``dim_turvar``/``dim_turth`` and
``variable.decimal``/``last_update``.

Idempotent: every upsert only writes rows whose content actually changes (``IS DISTINCT FROM``
guards), so loading the same response twice writes nothing.

Ordering by ``last_update`` (the response's own change stamp):

- each observation carries the ``last_update`` it came from and is only overwritten by a response
  with an equal or newer stamp (equal: the latest fetch wins), never by an older or unstamped one;
  cells not stored yet are always inserted;
- ``variable.decimal``/``last_update`` and dimension labels are only updated by a response that
  isn't older than ``variable.last_update`` (a *stale* response may still add missing dim items).

Windows of one variable share its ``last_update``, so equal stamps must (and do) load.

Entries the parser couldn't place (``unmatched``/``ambiguous``/``non_numeric``) are not loaded;
their counts are logged as a warning and returned in :class:`LoadStats`.

A ``data-availability: not-available`` response loads nothing and completes the task. API errors
(e.g. more than 3 periods) raise :class:`~bps_fetcher.client.BpsApiError` and fail the task.
"""

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Table, and_, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import Insert, insert

from bps_fetcher.db.schema import dim_turth, dim_turvar, dim_vervar, observation, variable
from bps_fetcher.paginate import NOT_AVAILABLE
from bps_fetcher.parse_data import DimItem, Observation, ParsedData, parse_data
from bps_fetcher.worker import HandlerResult, RawResponse, TaskContext, register

log = logging.getLogger(__name__)

KIND = "data"
MODEL = "data"
# Rows per INSERT ... VALUES statement (9 bind params each; well under Postgres' 65535 limit).
BATCH_SIZE = 2000


@dataclass(frozen=True, slots=True)
class LoadStats:
    """Rows actually inserted or changed, plus the parser's report bucket sizes."""

    observations: int = 0
    dims: int = 0
    variable: bool = False
    stale: bool = False
    unmatched: int = 0
    ambiguous: int = 0
    non_numeric: int = 0


def _batches(rows: Sequence[dict[str, Any]], size: int) -> Iterator[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _count(conn: Connection, stmt: Insert) -> int:
    """Rows inserted or updated (skipped conflicts return nothing). ``rowcount`` isn't reliable
    for multi-row ``INSERT ... VALUES`` through SQLAlchemy, ``RETURNING`` is."""
    return len(conn.execute(stmt.returning(literal_column("1"))).all())


def _upsert_dims(
    conn: Connection,
    table: Table,
    domain_id: str,
    var_id: int,
    items: Iterable[DimItem],
    *,
    update_labels: bool,
) -> int:
    cols = ["label", "group_label"] if "group_label" in table.c else ["label"]
    by_val: dict[int, dict[str, Any]] = {}
    for item in items:
        row = {"domain_id": domain_id, "var_id": var_id, "val": item.val, "label": item.label}
        if "group_label" in cols:
            row["group_label"] = item.group_label
        by_val[item.val] = row
    if not by_val:
        return 0
    stmt = insert(table).values(list(by_val.values()))
    keys = [table.c.domain_id, table.c.var_id, table.c.val]
    if not update_labels:
        return _count(conn, stmt.on_conflict_do_nothing(index_elements=keys))
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=keys,
        set_={c: ex[c] for c in cols},
        where=or_(*(table.c[c].is_distinct_from(ex[c]) for c in cols)),
    )
    return _count(conn, stmt)


def _upsert_observations(
    conn: Connection,
    domain_id: str,
    var_id: int,
    observations: Iterable[Observation],
    last_update: datetime | None,
) -> int:
    rows = [
        {
            "domain_id": domain_id,
            "var_id": var_id,
            "vervar": o.vervar,
            "turvar": o.turvar,
            "th": o.th,
            "turth": o.turth,
            "value": o.value,
            "last_update": last_update,
        }
        for o in observations
    ]
    changed = 0
    for batch in _batches(rows, BATCH_SIZE):
        stmt = insert(observation).values(list(batch))
        ex = stmt.excluded
        c = observation.c
        stmt = stmt.on_conflict_do_update(
            constraint=observation.primary_key,
            set_={"value": ex.value, "last_update": ex.last_update, "fetched_at": func.now()},
            where=and_(
                or_(c.last_update.is_(None), ex.last_update >= c.last_update),
                or_(
                    c.value.is_distinct_from(ex.value),
                    c.last_update.is_distinct_from(ex.last_update),
                ),
            ),
        )
        changed += _count(conn, stmt)
    return changed


def load(conn: Connection, domain_id: str, parsed: ParsedData) -> LoadStats:
    """Upsert one parsed data response. The ``variable`` row is locked for the transaction."""
    var_id = parsed.var_id
    if var_id is None:
        raise ValueError("data response has no var")
    key = and_(variable.c.domain_id == domain_id, variable.c.var_id == var_id)
    current = conn.execute(
        select(variable.c.decimal, variable.c.last_update).where(key).with_for_update()
    ).one_or_none()
    if current is None:
        raise LookupError(f"variable {domain_id}/{var_id} not in the variable table; run var_list")

    lu = parsed.last_update
    stale = current.last_update is not None and (lu is None or lu < current.last_update)

    var_changed = False
    if not stale:
        new = {
            "decimal": current.decimal if parsed.decimal is None else parsed.decimal,
            "last_update": current.last_update if lu is None else lu,
        }
        if new != {"decimal": current.decimal, "last_update": current.last_update}:
            conn.execute(update(variable).where(key).values(**new))
            var_changed = True

    d = parsed.dims
    dims = sum(
        _upsert_dims(conn, table, domain_id, var_id, items, update_labels=not stale)
        for table, items in (
            (dim_vervar, d.vervar),
            (dim_turvar, d.turvar),
            (dim_turth, d.turtahun),
        )
    )
    obs = _upsert_observations(conn, domain_id, var_id, parsed.observations, lu)
    return LoadStats(
        observations=obs,
        dims=dims,
        variable=var_changed,
        stale=stale,
        unmatched=len(parsed.unmatched),
        ambiguous=len(parsed.ambiguous),
        non_numeric=len(parsed.non_numeric),
    )


def _params(params: dict[str, Any]) -> tuple[str, int, str]:
    domain_id = params.get("domain")
    if not isinstance(domain_id, str) or not domain_id:
        raise ValueError(f"data needs a 'domain' param (got {domain_id!r})")
    var_id = params.get("var")
    if not isinstance(var_id, int) or isinstance(var_id, bool):
        raise ValueError(f"data needs an int 'var' param (got {var_id!r})")
    th = params.get("th")
    if not isinstance(th, str) or not th:
        raise ValueError(f"data needs a 'th' param (got {th!r})")
    return domain_id, var_id, th


def _sample(keys: Iterable[str], n: int = 5) -> str:
    keys = list(keys)
    more = f" (+{len(keys) - n} more)" if len(keys) > n else ""
    return ", ".join(keys[:n]) + more


@register(KIND)
async def data(ctx: TaskContext) -> HandlerResult:
    domain_id, var_id, th = _params(ctx.params)
    known = select(variable.c.var_id).where(
        and_(variable.c.domain_id == domain_id, variable.c.var_id == var_id)
    )
    if ctx.conn.execute(known).first() is None:
        raise LookupError(f"variable {domain_id}/{var_id} not in the variable table; run var_list")

    request = {"model": MODEL, "domain": domain_id, "var": var_id, "th": th}
    body = await ctx.client.get("list", **request)
    result = HandlerResult(raw=[RawResponse("list", request, body)])
    where = f"{domain_id}/{var_id} th={th}"
    if body.get("data-availability") == NOT_AVAILABLE:
        log.info("data %s: not available", where)
        return result

    parsed = parse_data(body)
    if parsed.var_id != var_id:
        raise ValueError(f"data {where}: response is for var {parsed.var_id!r}")
    stats = load(ctx.conn, domain_id, parsed)

    log.info(
        "data %s: %d observations, %d changed; %d dim rows changed%s",
        where,
        len(parsed.observations),
        stats.observations,
        stats.dims,
        "; stale response (older last_update), newer values kept" if stats.stale else "",
    )
    if stats.unmatched or stats.ambiguous or stats.non_numeric:
        log.warning(
            "data %s: %d unmatched, %d ambiguous, %d non-numeric datacontent entries not loaded"
            " [unmatched: %s] [ambiguous: %s] [non-numeric: %s]",
            where,
            stats.unmatched,
            stats.ambiguous,
            stats.non_numeric,
            _sample(parsed.unmatched),
            _sample(parsed.ambiguous),
            _sample(parsed.non_numeric),
        )
    return result
