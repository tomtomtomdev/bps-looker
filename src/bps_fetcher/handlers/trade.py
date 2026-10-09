"""``trade`` task: fetch one ``dataexim/`` response and replace its scope in ``trade_flow``.

Task params (see :func:`seed_trade`): ``flow`` (1 export / 2 import = API ``sumber``),
``period_type`` (1 monthly / 2 annual = API ``periode``), ``year`` (int, >= 2014) and
``chapters`` (2-digit HS chapters joined by ``;``, e.g. ``"01;02;03"`` from
:func:`~bps_fetcher.trade.batch_chapters`). Other params (e.g. a ``run`` label) are ignored.
One call ``dataexim/?sumber&periode&kodehs&jenishs=1&tahun`` per task; the response is kept
as a raw response.

Loading, inside the task's transaction:

- ``hs_chapter`` gets each chapter's description (see :func:`upsert_hs_chapters` for which text
  wins: latest year, then export over import — 2014 import labels are Indonesian);
- all ``trade_flow`` rows of the scope ``(flow, period_type, year, hs2 in chapters)`` are deleted
  and the response's rows inserted, so a reload drops rows that vanished upstream and other
  scopes (chapter batches, flows, period types, years) are never touched. A failing task rolls
  back as a whole: the old rows stay.

Key sentinels (PK columns can't be NULL): annual rows have ``month = 0``; a missing port
(``pod: null``, seen in real responses) is stored as ``''``. Rows sharing a key within one
response are summed (none seen so far). Rows outside the requested scope (other chapter/year,
month on an annual row, no month on a monthly one) fail the task.

``data-availability: unavailable`` (e.g. years before 2014, chapter 77) — or ``not-available`` —
loads nothing and **keeps** existing rows; the task completes.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, and_, delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bps_fetcher import queue
from bps_fetcher.db.schema import hs_chapter, trade_flow
from bps_fetcher.paginate import NOT_AVAILABLE_VALUES
from bps_fetcher.trade import (
    ALL_CHAPTERS,
    DEFAULT_BATCH_SIZE,
    EARLIEST_YEAR,
    EXPORT,
    IMPORT,
    MONTHLY,
    YEARLY,
    batch_chapters,
    parse_hs,
    parse_month,
)
from bps_fetcher.worker import HandlerResult, RawResponse, TaskContext, register

log = logging.getLogger(__name__)

KIND = "trade"
PATH = "dataexim/"
JENISHS_CHAPTER = 1  # 2-digit HS chapters
ANNUAL_MONTH = 0  # ``trade_flow.month`` of annual rows
UNAVAILABLE = frozenset({"unavailable"}) | NOT_AVAILABLE_VALUES
FLOWS = (EXPORT, IMPORT)
PERIOD_TYPES = (MONTHLY, YEARLY)

Key = tuple[int, int, int, int, str, str, str]


@dataclass(frozen=True, slots=True)
class TradeScope:
    """What one ``trade`` task covers (and replaces)."""

    flow: int
    period_type: int
    year: int
    chapters: tuple[str, ...]

    @property
    def kodehs(self) -> str:
        return ";".join(self.chapters)

    def request(self) -> dict[str, Any]:
        return {
            "sumber": self.flow,
            "periode": self.period_type,
            "kodehs": self.kodehs,
            "jenishs": JENISHS_CHAPTER,
            "tahun": self.year,
        }


@dataclass(slots=True)
class ParsedTrade:
    available: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    descriptions: dict[str, str] = field(default_factory=dict)  # hs2 -> description


def _int(params: Mapping[str, Any], name: str, allowed: Iterable[int] | None = None) -> int:
    value = params.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"trade needs an int {name!r} param (got {value!r})")
    if allowed is not None and value not in allowed:
        raise ValueError(f"trade {name!r} must be one of {tuple(allowed)} (got {value!r})")
    return value


def _check_chapters(chapters: Sequence[str]) -> tuple[str, ...]:
    if not chapters:
        raise ValueError("trade needs at least one chapter")
    bad = [c for c in chapters if len(c) != 2 or not c.isdigit()]
    if bad:
        raise ValueError(f"trade chapters must be 2-digit codes, got {bad}")
    if len(set(chapters)) != len(chapters):
        raise ValueError(f"duplicate trade chapters: {list(chapters)}")
    return tuple(chapters)


def scope_of(params: Mapping[str, Any]) -> TradeScope:
    """Validate task params into a :class:`TradeScope` (``ValueError`` if malformed)."""
    flow = _int(params, "flow", FLOWS)
    period_type = _int(params, "period_type", PERIOD_TYPES)
    year = _int(params, "year")
    if year < EARLIEST_YEAR:
        raise ValueError(f"trade data starts in {EARLIEST_YEAR} (got year {year})")
    chapters = params.get("chapters")
    if not isinstance(chapters, str) or not chapters:
        raise ValueError(f"trade needs a 'chapters' param like '01;02' (got {chapters!r})")
    return TradeScope(flow, period_type, year, _check_chapters(chapters.split(";")))


def _amount(value: Any, what: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} is not a number: {value!r}")
    if isinstance(value, float):
        value = repr(value)  # keep the JSON digits, not the binary expansion
    try:
        d = Decimal(value) if isinstance(value, int) else Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{what} is not a number: {value!r}") from None
    if not d.is_finite():
        raise ValueError(f"{what} is not a finite number: {value!r}")
    return d


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


def parse_trade(body: Mapping[str, Any], scope: TradeScope) -> ParsedTrade:
    """Turn a ``dataexim/`` response into ``trade_flow`` rows for ``scope``.

    Raises ``ValueError`` for rows that don't belong to the scope or can't be parsed.
    """
    if body.get("data-availability") in UNAVAILABLE:
        return ParsedTrade(available=False)
    data = body.get("data")
    if not isinstance(data, list):
        raise ValueError(f"trade response has no 'data' list (got {type(data).__name__})")
    wanted = set(scope.chapters)
    monthly = scope.period_type == MONTHLY
    parsed = ParsedTrade(available=True)
    rows: dict[Key, dict[str, Any]] = {}
    duplicates = 0
    for i, item in enumerate(data):
        where = f"trade row {i}"
        if not isinstance(item, Mapping):
            raise ValueError(f"{where} is not an object: {item!r}")
        hs2, description = parse_hs(str(item.get("kodehs", "")))
        if hs2 not in wanted:
            raise ValueError(f"{where}: chapter {hs2} not requested ({scope.kodehs})")
        if str(item.get("tahun")) != str(scope.year):
            raise ValueError(f"{where}: year {item.get('tahun')!r}, expected {scope.year}")
        bulan = item.get("bulan")
        if monthly:
            if not isinstance(bulan, str):
                raise ValueError(f"{where}: monthly row without 'bulan'")
            month = parse_month(bulan)[0]
        else:
            if bulan is not None:
                raise ValueError(f"{where}: annual row has 'bulan' {bulan!r}")
            month = ANNUAL_MONTH
        port = item.get("pod")
        if port is not None and not isinstance(port, str):
            raise ValueError(f"{where}: bad port {port!r}")
        country = item.get("ctr")
        if not isinstance(country, str) or not country:
            raise ValueError(f"{where}: missing country (ctr={country!r})")
        value = _amount(item.get("value"), f"{where} value")
        weight = _amount(item.get("netweight"), f"{where} netweight")

        parsed.descriptions.setdefault(hs2, description)
        key: Key = (scope.flow, scope.period_type, scope.year, month, hs2, port or "", country)
        row = rows.get(key)
        if row is None:
            rows[key] = {
                "flow": scope.flow,
                "period_type": scope.period_type,
                "year": scope.year,
                "month": month,
                "hs2": hs2,
                "port": port or "",
                "country": country,
                "value_usd": value,
                "netweight_kg": weight,
            }
        else:
            duplicates += 1
            row["value_usd"] = _add(row["value_usd"], value)
            row["netweight_kg"] = _add(row["netweight_kg"], weight)
    if duplicates:
        log.warning("trade %s: %d rows shared a key with another row; summed", scope, duplicates)
    parsed.rows = list(rows.values())
    return parsed


def upsert_hs_chapters(
    conn: Connection, descriptions: Mapping[str, str], *, flow: int, year: int
) -> int:
    """Record chapter descriptions seen in a ``(flow, year)`` response; returns rows written.

    Which text wins: a later ``year`` replaces an earlier one; within the same year an export
    (English) replaces an import (2014 imports are Indonesian) but not the other way round; the
    same (year, flow) rank takes the latest fetch. An older year never overwrites.
    """
    if not descriptions:
        return 0
    stmt = pg_insert(hs_chapter).values(
        [
            {"hs2": hs2, "description": d, "source_flow": flow, "source_year": year}
            for hs2, d in sorted(descriptions.items())
        ]
    )
    ex, t = stmt.excluded, hs_chapter.c
    stmt = stmt.on_conflict_do_update(
        index_elements=[t.hs2],
        set_={
            "description": ex.description,
            "source_flow": ex.source_flow,
            "source_year": ex.source_year,
        },
        where=(ex.source_year > t.source_year)
        | and_(
            ex.source_year == t.source_year,
            ex.source_flow <= t.source_flow,  # EXPORT (1) ranks above IMPORT (2)
            (ex.description != t.description) | (ex.source_flow != t.source_flow),
        ),
    )
    return len(conn.execute(stmt.returning(t.hs2)).all())


def replace_scope(conn: Connection, scope: TradeScope, rows: Sequence[Mapping[str, Any]]) -> int:
    """Delete every ``trade_flow`` row of ``scope`` and insert ``rows``; returns rows deleted."""
    c = trade_flow.c
    deleted = conn.execute(
        delete(trade_flow).where(
            c.flow == scope.flow,
            c.period_type == scope.period_type,
            c.year == scope.year,
            c.hs2.in_(scope.chapters),
        )
    ).rowcount
    if rows:
        conn.execute(insert(trade_flow), list(rows))
    return int(deleted or 0)


@register(KIND)
async def trade(ctx: TaskContext) -> HandlerResult:
    scope = scope_of(ctx.params)
    request = scope.request()
    body = await ctx.client.get(PATH, **request)
    result = HandlerResult(raw=[RawResponse(PATH, request, body)])
    parsed = parse_trade(body, scope)
    if not parsed.available:
        log.info("trade %s: unavailable; existing rows kept", scope)
        return result
    upsert_hs_chapters(ctx.conn, parsed.descriptions, flow=scope.flow, year=scope.year)
    deleted = replace_scope(ctx.conn, scope, parsed.rows)
    log.info("trade %s: %d rows loaded (%d replaced)", scope, len(parsed.rows), deleted)
    return result


def seed_trade(
    conn: Connection,
    from_year: int,
    to_year: int,
    *,
    flows: Sequence[int] = FLOWS,
    period_types: Sequence[int] = PERIOD_TYPES,
    batch_size: int = DEFAULT_BATCH_SIZE,
    run: str | None = None,
) -> int:
    """Enqueue one ``trade`` task per flow x period type x year x chapter batch; returns how
    many were newly enqueued.

    Chapters are :data:`~bps_fetcher.trade.ALL_CHAPTERS` (01-99 minus 77) in batches of
    ``batch_size``. Tasks are idempotent on their params, so re-seeding enqueues nothing; pass a
    ``run`` label (e.g. today's date) to schedule a fresh reload (it is ignored by the handler).
    """
    if from_year < EARLIEST_YEAR:
        raise ValueError(f"trade data starts in {EARLIEST_YEAR} (got from {from_year})")
    if to_year < from_year:
        raise ValueError(f"empty year range {from_year}..{to_year}")
    for name, values, allowed in (
        ("flow", flows, FLOWS),
        ("period type", period_types, PERIOD_TYPES),
    ):
        bad = [v for v in values if v not in allowed]
        if bad or not values:
            raise ValueError(f"bad trade {name}s {list(values)} (allowed: {allowed})")
    batches = batch_chapters(ALL_CHAPTERS, batch_size)
    enqueued = 0
    for flow in dict.fromkeys(flows):
        for period_type in dict.fromkeys(period_types):
            for year in range(from_year, to_year + 1):
                for chapters in batches:
                    params: dict[str, Any] = {
                        "flow": flow,
                        "period_type": period_type,
                        "year": year,
                        "chapters": chapters,
                    }
                    if run is not None:
                        params["run"] = run
                    if queue.enqueue(conn, KIND, params) is not None:
                        enqueued += 1
    return enqueued
