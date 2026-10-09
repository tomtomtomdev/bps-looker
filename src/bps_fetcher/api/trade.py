"""Trade dashboard (U6): totals, top-N breakdowns and monthly series of foreign trade.

Everything reads ``mv_trade_rollup`` (migration 0011: totals per period by ``grp`` = ``total`` /
``hs2`` / ``country`` / ``port``), a few hundred rows per period, through its
``(grp, flow, period_type, year, month, key)`` and ``(grp, key, …)`` indexes — except a series
filtered by chapter **and** country, which reads ``trade_flow`` through its ``(hs2, year)`` and
``(country, year)`` indexes. The rollup is refreshed by the worker after trade tasks
(``bps_fetcher.rollup``).

**Period ranges** (``from`` / ``to``, each ``YYYY`` or ``YYYY-MM``; one alone means both):

- Both years → *year mode*: each year of the range counts its **annual** rows (``period_type``
  2, the figures BPS publishes for the year — for the current year a year-to-date total) when
  the flow has any for that year, otherwise the sum of its **monthly** rows (a partial year, e.g.
  Jan-Mar). Annual and monthly figures are never added together.
- Otherwise → *month mode*: monthly rows with ``(year, month)`` in the range; a year endpoint
  stands for its January (``from``) or December (``to``).

``periods`` in the responses says which basis each year used and how many months of monthly
data it has (so a UI can say "Jan-Mar" or "year to date"). Country / port ``''`` (BPS sent none)
come back as key ``''`` with no label.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    Connection,
    Select,
    and_,
    false,
    func,
    literal,
    or_,
    select,
    table,
    tuple_,
)
from sqlalchemy.sql import column

from bps_fetcher.db.schema import hs_chapter, trade_flow

FlowName = Literal["export", "import"]
BreakdownBy = Literal["hs2", "country", "port"]
Granularity = Literal["year", "month"]

FLOWS: dict[FlowName, int] = {"export": 1, "import": 2}
FLOW_NAMES: dict[int, FlowName] = {v: k for k, v in FLOWS.items()}
MONTHLY, ANNUAL = 1, 2

mv = table(
    "mv_trade_rollup",
    *(
        column(c)
        for c in (
            "grp", "flow", "period_type", "year", "month", "key", "value_usd", "netweight_kg",
            "n_rows",
        )
    ),
)  # fmt: skip

_PERIOD = re.compile(r"^(\d{4})(?:-(0[1-9]|1[0-2]))?$")
PERIOD_PATTERN = _PERIOD.pattern


@dataclass(frozen=True, slots=True)
class PeriodRange:
    granularity: Granularity
    start: tuple[int, int]
    end: tuple[int, int]

    def as_dict(self) -> dict[str, str]:
        def fmt(ym: tuple[int, int]) -> str:
            return f"{ym[0]}" if self.granularity == "year" else f"{ym[0]}-{ym[1]:02d}"

        return {"start": fmt(self.start), "end": fmt(self.end), "granularity": self.granularity}

    @property
    def years(self) -> range:
        return range(self.start[0], self.end[0] + 1)

    def contains(self, year: int, month: int) -> bool:
        return self.start <= (year, month) <= self.end


def _parse_period(value: str) -> tuple[int, int | None]:
    m = _PERIOD.match(value)
    if not m:
        raise ValueError(f"{value!r}: expected YYYY or YYYY-MM")
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def parse_range(start: str | None, end: str | None) -> PeriodRange:
    """``from`` / ``to`` → a :class:`PeriodRange` (module doc); ``ValueError`` when invalid."""
    if start is None and end is None:
        raise ValueError("give `from` and/or `to`")
    (y0, m0), (y1, m1) = _parse_period(start or end or ""), _parse_period(end or start or "")
    if m0 is None and m1 is None:
        rng = PeriodRange("year", (y0, 1), (y1, 12))
    else:
        rng = PeriodRange("month", (y0, m0 or 1), (y1, m1 or 12))
    if rng.start > rng.end:
        raise ValueError("`from` is after `to`")
    return rng


def year_range(year: int) -> PeriodRange:
    return PeriodRange("year", (year, 1), (year, 12))


def month_range(year: int, month: int) -> PeriodRange:
    return PeriodRange("month", (year, month), (year, month))


# --- what exists ---------------------------------------------------------------------------------


@dataclass(slots=True)
class YearData:
    annual: bool = False
    months: set[int] = field(default_factory=set)


Availability = dict[tuple[int, int], YearData]
"""``(flow, year)`` → whether annual rows exist and which months have monthly rows."""


def availability(conn: Connection, flows: Iterable[int] | None = None) -> Availability:
    query = select(mv.c.flow, mv.c.year, mv.c.month).where(mv.c.grp == "total")
    if flows is not None:
        query = query.where(mv.c.flow.in_(list(flows)))
    out: Availability = {}
    for flow, year, month in conn.execute(query):
        data = out.setdefault((flow, year), YearData())
        if month == 0:
            data.annual = True
        else:
            data.months.add(month)
    return out


def load_periods(conn: Connection) -> dict[str, Any]:
    avail = availability(conn)
    items = [
        {"flow": FLOW_NAMES[flow], "year": year, "annual": d.annual, "months": sorted(d.months)}
        for (flow, year), d in sorted(avail.items())
    ]
    monthly = [(year, max(d.months)) for (_, year), d in avail.items() if d.months]
    latest_month = max(monthly) if monthly else None
    return {
        "items": items,
        "latest_year": max((year for _, year in avail), default=None),
        "latest_month": f"{latest_month[0]}-{latest_month[1]:02d}" if latest_month else None,
    }


def default_range(avail: Availability, flows: Iterable[int]) -> PeriodRange | None:
    """The latest year any of ``flows`` has data for (year mode), or ``None`` without data."""
    wanted = set(flows)
    year = max((y for f, y in avail if f in wanted), default=None)
    return None if year is None else year_range(year)


# --- scope: which rows a (flow, range) reads -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    clause: ColumnElement[bool]
    periods: list[dict[str, Any]]


def scope(rng: PeriodRange | None, flow: int, avail: Availability) -> Scope:
    """Row filter on ``mv_trade_rollup`` for one flow over ``rng`` (module doc) + its basis."""
    if rng is None:
        return Scope(false(), [])
    periods: list[dict[str, Any]] = []
    if rng.granularity == "month":
        for year in rng.years:
            months = [
                m for m in avail.get((flow, year), YearData()).months if rng.contains(year, m)
            ]
            if months:
                periods.append({"year": year, "basis": "monthly", "months": len(months)})
        clause = and_(
            mv.c.period_type == MONTHLY,
            tuple_(mv.c.year, mv.c.month) >= tuple_(literal(rng.start[0]), literal(rng.start[1])),
            tuple_(mv.c.year, mv.c.month) <= tuple_(literal(rng.end[0]), literal(rng.end[1])),
        )
        return Scope(clause, periods)
    annual, monthly = [], []
    for year in rng.years:
        data = avail.get((flow, year))
        if data is None:
            continue
        if data.annual:
            annual.append(year)
            periods.append({"year": year, "basis": "annual", "months": len(data.months) or None})
        elif data.months:
            monthly.append(year)
            periods.append({"year": year, "basis": "monthly", "months": len(data.months)})
    parts = []
    if annual:
        parts.append(and_(mv.c.period_type == ANNUAL, mv.c.year.in_(annual)))
    if monthly:
        parts.append(and_(mv.c.period_type == MONTHLY, mv.c.year.in_(monthly)))
    return Scope(or_(*parts) if parts else false(), periods)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _sum(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


# --- summary -------------------------------------------------------------------------------------


def load_summary(conn: Connection, flows: Sequence[int], rng: PeriodRange | None) -> dict[str, Any]:
    avail = availability(conn, flows)
    if rng is None:
        rng = default_range(avail, flows)
    items = []
    values: dict[int, Decimal | None] = {}
    for flow in flows:
        sc = scope(rng, flow, avail)
        value, weight = conn.execute(
            select(func.sum(mv.c.value_usd), func.sum(mv.c.netweight_kg)).where(
                mv.c.grp == "total", mv.c.flow == flow, sc.clause
            )
        ).one()
        values[flow] = value
        items.append(
            {
                "flow": FLOW_NAMES[flow],
                "value_usd": _float(value),
                "netweight_kg": _float(weight),
                "periods": sc.periods,
            }
        )
    exports, imports = values.get(FLOWS["export"]), values.get(FLOWS["import"])
    balance = None if exports is None or imports is None else exports - imports
    return {
        "range": rng.as_dict() if rng else None,
        "items": items,
        "balance_usd": _float(balance),
    }


# --- breakdown -----------------------------------------------------------------------------------


def breakdown_query(by: BreakdownBy, flow: int, clause: ColumnElement[bool]) -> Select[Any]:
    """Every key of ``by`` with its totals over the scope, largest first."""
    value = func.sum(mv.c.value_usd).label("value_usd")
    return (
        select(mv.c.key, value, func.sum(mv.c.netweight_kg).label("netweight_kg"))
        .where(mv.c.grp == by, mv.c.flow == flow, clause)
        .group_by(mv.c.key)
        .order_by(value.desc().nulls_last(), mv.c.key)
    )


def _labels(conn: Connection, by: BreakdownBy, keys: Sequence[str]) -> Mapping[str, str | None]:
    if by != "hs2":
        return {k: k or None for k in keys}
    rows = conn.execute(
        select(hs_chapter.c.hs2, hs_chapter.c.description).where(hs_chapter.c.hs2.in_(keys))
    )
    return {r.hs2: r.description for r in rows}


def load_breakdown(
    conn: Connection, by: BreakdownBy, flow: int, rng: PeriodRange | None, top: int
) -> dict[str, Any]:
    avail = availability(conn, [flow])
    if rng is None:
        rng = default_range(avail, [flow])
    sc = scope(rng, flow, avail)
    rows = conn.execute(breakdown_query(by, flow, sc.clause)).all()
    total = _sum(r.value_usd for r in rows)
    total_kg = _sum(r.netweight_kg for r in rows)

    def share(value: Decimal | None) -> float | None:
        return None if value is None or not total else float(value / total)

    head, rest = rows[:top], rows[top:]
    labels = _labels(conn, by, [r.key for r in head])
    items = [
        {
            "key": r.key,
            "label": labels.get(r.key),
            "value_usd": _float(r.value_usd),
            "netweight_kg": _float(r.netweight_kg),
            "share": share(r.value_usd),
        }
        for r in head
    ]
    others = None
    if rest:
        value = _sum(r.value_usd for r in rest)
        others = {
            "count": len(rest),
            "value_usd": _float(value),
            "netweight_kg": _float(_sum(r.netweight_kg for r in rest)),
            "share": share(value),
        }
    return {
        "by": by,
        "flow": FLOW_NAMES[flow],
        "top": top,
        "range": rng.as_dict() if rng else None,
        "total": {
            "flow": FLOW_NAMES[flow],
            "value_usd": _float(total),
            "netweight_kg": _float(total_kg),
            "periods": sc.periods,
        },
        "items": items,
        "others": others,
    }


# --- monthly series ------------------------------------------------------------------------------


def _month_bounds(cols: Sequence[Any], rng: PeriodRange | None) -> list[ColumnElement[bool]]:
    if rng is None:
        return []
    ym = tuple_(*cols)
    return [
        ym >= tuple_(literal(rng.start[0]), literal(rng.start[1])),
        ym <= tuple_(literal(rng.end[0]), literal(rng.end[1])),
    ]


def series_query(
    flows: Sequence[int], hs2: str | None, country: str | None, rng: PeriodRange | None
) -> Select[Any]:
    """Monthly totals per flow: from the rollup, or ``trade_flow`` when both filters are set."""
    if hs2 is not None and country is not None:
        t = trade_flow.c
        return (
            select(
                t.flow, t.year, t.month,
                func.sum(t.value_usd).label("value_usd"),
                func.sum(t.netweight_kg).label("netweight_kg"),
            )
            .where(
                t.period_type == MONTHLY, t.hs2 == hs2, t.country == country, t.flow.in_(flows),
                *_month_bounds((t.year, t.month), rng),
            )
            .group_by(t.flow, t.year, t.month)
            .order_by(t.flow, t.year, t.month)
        )  # fmt: skip
    grp, key = ("hs2", hs2) if hs2 is not None else ("country", country)
    if key is None:
        grp, key = "total", ""
    return (
        select(mv.c.flow, mv.c.year, mv.c.month, mv.c.value_usd, mv.c.netweight_kg)
        .where(
            mv.c.grp == grp, mv.c.key == key, mv.c.flow.in_(flows), mv.c.period_type == MONTHLY,
            *_month_bounds((mv.c.year, mv.c.month), rng),
        )
        .order_by(mv.c.flow, mv.c.year, mv.c.month)
    )  # fmt: skip


def load_series(
    conn: Connection,
    flows: Sequence[int],
    *,
    hs2: str | None,
    country: str | None,
    rng: PeriodRange | None,
) -> dict[str, Any]:
    points: dict[int, list[dict[str, Any]]] = {flow: [] for flow in flows}
    by_month: dict[int, dict[tuple[int, int], Decimal | None]] = {flow: {} for flow in flows}
    for r in conn.execute(series_query(flows, hs2, country, rng)):
        by_month[r.flow][(r.year, r.month)] = r.value_usd
        points[r.flow].append(
            {
                "period": f"{r.year}-{r.month:02d}",
                "date": date(r.year, r.month, 1),
                "value_usd": _float(r.value_usd),
                "netweight_kg": _float(r.netweight_kg),
            }
        )
    balance = []
    exports, imports = by_month.get(FLOWS["export"]), by_month.get(FLOWS["import"])
    if exports is not None and imports is not None:
        for ym in sorted(exports.keys() & imports.keys()):
            e, i = exports[ym], imports[ym]
            if e is not None and i is not None:
                balance.append(
                    {
                        "period": f"{ym[0]}-{ym[1]:02d}",
                        "date": date(*ym, 1),
                        "value_usd": float(e - i),
                    }
                )
    hs2_label = None
    if hs2 is not None:
        hs2_label = conn.execute(
            select(hs_chapter.c.description).where(hs_chapter.c.hs2 == hs2)
        ).scalar_one_or_none()
    return {
        "hs2": hs2,
        "hs2_label": hs2_label,
        "country": country,
        "range": rng.as_dict() if rng else None,
        "series": [{"flow": FLOW_NAMES[flow], "points": points[flow]} for flow in flows],
        "balance": balance,
    }
