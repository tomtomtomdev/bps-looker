"""``indicators`` task: page through ``/list?model=indicators`` for one domain and record a
snapshot of every strategic indicator in ``indicator_snapshot``.

Task params: ``domain`` (required, 4-digit id; the ``domain`` row must already exist). Any other
param (e.g. ``run``, see :func:`seed_indicators`) is ignored by the handler. Every page's response
is kept as a raw response.

Only national (``pusat``) and province (``prov``) domains serve indicators. For a regency
(``kab``) domain the handler is a no-op: no API call, nothing stored, the task completes.

The API only returns each indicator's **latest** value, so history is built here: rows are keyed
by ``(domain_id, indicator_id, periode, title)``. A new period inserts a new row; re-seeing a row
only moves ``last_seen`` forward (``first_seen`` never changes) and picks up revised values.
"""

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Connection, exists, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert

from bps_fetcher import queue
from bps_fetcher.db.schema import domain, indicator_snapshot
from bps_fetcher.handlers.domains import domain_level
from bps_fetcher.paginate import is_not_available, items_of, paginate_pages
from bps_fetcher.worker import HandlerResult, RawResponse, TaskContext, register

log = logging.getLogger(__name__)

KIND = "indicators"
MODEL = "indicators"
LEVELS = ("pusat", "prov")

# Non-key columns taken from the latest sighting.
_COLUMNS = ("var", "subject_csa", "name", "value", "unit", "category", "data_source")


class IndicatorItem(BaseModel):
    """One item of ``/list?model=indicators``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    indicator_id: int
    title: str
    periode: str = ""
    var: int | None = None
    subject_csa: int | None = None
    name: str | None = None
    value: Decimal | None = None
    unit: str | None = None
    category: int | None = None
    data_source: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _number(cls, v: Any) -> Any:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, float):
            return Decimal(repr(v))
        if isinstance(v, str):
            try:
                d = Decimal(v.strip())
            except InvalidOperation:
                return None
            return d if d.is_finite() else None
        return v

    @field_validator("name", "unit", "data_source", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return v or None

    def row(self, domain_id: str) -> dict[str, Any]:
        return {"domain_id": domain_id, **self.model_dump()}


def upsert_snapshots(
    conn: Connection,
    domain_id: str,
    items: Iterable[IndicatorItem],
    *,
    seen_at: datetime | None = None,
) -> int:
    """Record one sighting of ``items``; returns the number of **new** rows.

    ``seen_at`` (default: the transaction's ``now()``) sets ``first_seen`` on insert and moves
    ``last_seen`` forward (never back). Unchanged rows seen no later than before are skipped.
    """
    rows: dict[tuple[int, str, str], dict[str, Any]] = {}
    for item in items:
        rows[(item.indicator_id, item.periode, item.title)] = item.row(domain_id)
    if not rows:
        return 0
    seen = func.now() if seen_at is None else seen_at
    stmt = insert(indicator_snapshot).values(
        [{**r, "first_seen": seen, "last_seen": seen} for r in rows.values()]
    )
    ex = stmt.excluded
    t = indicator_snapshot.c
    stmt = stmt.on_conflict_do_update(
        constraint=indicator_snapshot.primary_key,
        set_={
            **{c: ex[c] for c in _COLUMNS},
            "last_seen": func.greatest(t.last_seen, ex.last_seen),
        },
        where=or_(
            ex.last_seen > t.last_seen,
            *(t[c].is_distinct_from(ex[c]) for c in _COLUMNS),
        ),
    )
    # xmax = 0 only for freshly inserted rows (updated rows carry the updating xid).
    inserted: Sequence[bool] = (
        conn.execute(stmt.returning(literal_column("xmax = 0"))).scalars().all()
    )
    return sum(1 for new in inserted if new)


def _domain_id(params: dict[str, Any]) -> str:
    value = params.get("domain")
    if not isinstance(value, str) or not value:
        raise ValueError(f"indicators needs a 'domain' param (got {value!r})")
    return value


@register(KIND)
async def indicators(ctx: TaskContext) -> HandlerResult:
    domain_id = _domain_id(ctx.params)
    level = domain_level(domain_id)
    result = HandlerResult()
    if level not in LEVELS:
        log.info("indicators %s: %s domain has no strategic indicators; skipped", domain_id, level)
        return result
    if not ctx.conn.execute(select(exists().where(domain.c.domain_id == domain_id))).scalar():
        raise LookupError(f"domain {domain_id!r} not in the domain table; run 'domains' first")

    items: list[IndicatorItem] = []
    async for request, body in paginate_pages(ctx.client, MODEL, domain=domain_id):
        result.raw.append(RawResponse("list", request, body))
        if is_not_available(body):
            break
        items.extend(IndicatorItem.model_validate(i) for i in items_of(body, MODEL)[1])

    new = upsert_snapshots(ctx.conn, domain_id, items)
    log.info("indicators %s: %d indicators, %d new snapshot rows", domain_id, len(items), new)
    return result


def seed_indicators(
    conn: Connection, domains: Sequence[str] | None = None, *, run: str | None = None
) -> int:
    """Enqueue one ``indicators`` task per domain; returns how many were newly enqueued.

    ``domains=None`` means every ``pusat``/``prov`` domain in the ``domain`` table (run the
    ``domains`` task first). Explicit ids are not looked up, but a ``kab`` (or malformed) id raises
    ``ValueError`` before anything is enqueued.

    Tasks are idempotent on their params, so re-seeding enqueues nothing once a domain's task
    exists. Pass a ``run`` label (e.g. today's date) to take a fresh snapshot: it is added to the
    params (``{"domain", "run"}``) and ignored by the handler.
    """
    if domains is None:
        ids = list(
            conn.execute(
                select(domain.c.domain_id)
                .where(domain.c.level.in_(LEVELS))
                .order_by(domain.c.domain_id)
            ).scalars()
        )
    else:
        ids = list(dict.fromkeys(domains))
        bad = [d for d in ids if domain_level(d) not in LEVELS]
        if bad:
            raise ValueError(f"indicators are only served for pusat/prov domains, not {bad}")
    enqueued = 0
    for domain_id in ids:
        params = {"domain": domain_id} if run is None else {"domain": domain_id, "run": run}
        if queue.enqueue(conn, KIND, params) is not None:
            enqueued += 1
    return enqueued
