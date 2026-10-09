"""Endpoints. Every route has an explicit ``operation_id`` and tag: the U1 TypeScript client is
generated from ``web/openapi.json`` and its function names must not drift."""

import logging
from typing import Annotated

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError

from bps_fetcher.api import trade
from bps_fetcher.api.deps import Conn, get_engine
from bps_fetcher.api.indicators import domain_row, load_history, load_indicators
from bps_fetcher.api.models import (
    CrossSection,
    Domain,
    DomainLevel,
    Freq,
    Health,
    IndicatorHistory,
    IndicatorList,
    SeriesResponse,
    TradeBreakdown,
    TradeBy,
    TradeFlow,
    TradePeriods,
    TradeSeriesResponse,
    TradeSummary,
    VariableDetail,
    VariablePage,
    VariableSummary,
)
from bps_fetcher.api.search import search_query
from bps_fetcher.api.series import (
    MAX_SERIES,
    dimensions,
    load_cross_section,
    load_series,
    variable_row,
)
from bps_fetcher.db.schema import domain
from bps_fetcher.redact import redact

log = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    operation_id="getHealth",
    tags=["meta"],
    summary="Liveness + database reachability",
    response_model=Health,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Health}},
)
def health(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> Health:
    """200 with the DB's Alembic revision, or 503 when the database can't be reached."""
    try:
        with engine.connect() as conn:
            revision = MigrationContext.configure(conn).get_current_revision()
    except DBAPIError as exc:
        log.warning("health: database unreachable: %s", redact(str(exc.orig or exc)))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="error", database="unreachable")
    return Health(status="ok", database="ok", revision=revision)


@router.get(
    "/domains",
    operation_id="listDomains",
    tags=["domains"],
    summary="BPS domains (national, provinces, regencies)",
    response_model=list[Domain],
)
def list_domains(
    conn: Conn,
    level: Annotated[DomainLevel | None, Query(description="Only domains at this level.")] = None,
) -> list[Domain]:
    """Every crawled domain, ordered by ``domain_id``; ``level`` filters pusat/prov/kab."""
    query = select(domain.c.domain_id, domain.c.name, domain.c.url, domain.c.level).order_by(
        domain.c.domain_id
    )
    if level is not None:
        query = query.where(domain.c.level == level)
    return [Domain.model_validate(row._mapping) for row in conn.execute(query)]


@router.get(
    "/variables",
    operation_id="searchVariables",
    tags=["variables"],
    summary="Search the dynamic-table variable catalog",
    response_model=VariablePage,
)
def search_variables(
    conn: Conn,
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Words to find in the title or subject (all must match, each as a "
            "prefix: `infl bul`). Empty → every variable, by title.",
        ),
    ] = None,
    domain: Annotated[
        str | None, Query(pattern=r"^\d{4}$", description="Only this domain (e.g. `0000`).")
    ] = None,
    level: Annotated[DomainLevel | None, Query(description="Only domains at this level.")] = None,
    subject: Annotated[int | None, Query(description="Only this subject (`subject_id`).")] = None,
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> VariablePage:
    """Full-text search ranked by relevance (title over subject), or all by title when ``q`` is
    empty; ``total`` counts every match."""
    rows, count = search_query(q, domain_id=domain, level=level, subject_id=subject)
    total = conn.execute(count).scalar_one()
    items = conn.execute(rows.limit(page_size).offset((page - 1) * page_size))
    return VariablePage(
        items=[VariableSummary.model_validate(r._mapping) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


DomainId = Annotated[str, Path(pattern=r"^\d{4}$", description="Domain id, e.g. `0000`.")]
VarId = Annotated[int, Path(description="Variable id within the domain.")]


def _require_variable(conn: Conn, domain_id: str, var_id: int) -> dict[str, object]:
    row = variable_row(conn, domain_id, var_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variable not found")
    return row


@router.get(
    "/variables/{domain}/{var}",
    operation_id="getVariable",
    tags=["variables"],
    summary="A variable's metadata and dimensions",
    response_model=VariableDetail,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown variable"}},
)
def get_variable(conn: Conn, domain: DomainId, var: VarId) -> VariableDetail:
    """Title, unit, subject, definition/notes (BPS HTML — sanitize before rendering), decimals,
    last update, plus every dimension member: vervar (regions/categories), turvar, turth
    (sub-periods with their ``freq`` and ``has_data``) and periods (``th`` = year)."""
    row = _require_variable(conn, domain, var)
    return VariableDetail.model_validate({**row, **dimensions(conn, domain, var)})


@router.get(
    "/variables/{domain}/{var}/series",
    operation_id="getVariableSeries",
    tags=["variables"],
    summary="Time series of a variable, one per vervar x turvar",
    response_model=SeriesResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown variable"}},
)
def get_variable_series(
    conn: Conn,
    domain: DomainId,
    var: VarId,
    vervar: Annotated[
        list[int] | None,
        Query(description="Vervar members (repeat the parameter); default: all."),
    ] = None,
    turvar: Annotated[
        list[int] | None,
        Query(description="Turvar members (repeat the parameter); default: all."),
    ] = None,
    turth: Annotated[
        list[int] | None,
        Query(description="Only these sub-periods, e.g. months `1`-`12`; default: all."),
    ] = None,
) -> SeriesResponse:
    """One series per vervar x turvar combination (request order), at most ``max_series``
    (``truncated`` when more were asked for); points sorted by time, each with its period
    (``2024-03``, ``2024-Q2``, ``2024-S1``, ``2024``) and start date."""
    _require_variable(conn, domain, var)
    series, truncated = load_series(conn, domain, var, vervars=vervar, turvars=turvar, turths=turth)
    return SeriesResponse.model_validate(
        {"series": series, "truncated": truncated, "max_series": MAX_SERIES}
    )


@router.get(
    "/variables/{domain}/{var}/cross-section",
    operation_id="getVariableCrossSection",
    tags=["variables"],
    summary="One value per region for one period (ranking + map)",
    response_model=CrossSection,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown variable"}},
)
def get_variable_cross_section(
    conn: Conn,
    domain: DomainId,
    var: VarId,
    th: Annotated[int | None, Query(description="Year (`th`); default: the latest.")] = None,
    turvar: Annotated[int | None, Query(description="Turvar member; default: the first.")] = None,
    turth: Annotated[
        int | None, Query(description="Sub-period; default: the latest of the year.")
    ] = None,
    freq: Annotated[
        Freq | None, Query(description="Only periods of this kind (slider + default).")
    ] = None,
) -> CrossSection:
    """Every vervar member's value for one (``th``, ``turth``) period — by default the latest
    with data (for ``turvar`` and ``freq``) — sorted by value, highest first (members without a
    value last, ``null``). The national aggregate (9999 / ``INDONESIA``) comes as ``national``,
    not ranked. ``periods`` lists every period with data, in time order, for a slider."""
    _require_variable(conn, domain, var)
    return CrossSection.model_validate(
        load_cross_section(conn, domain, var, th=th, turvar=turvar, turth=turth, freq=freq)
    )


@router.get(
    "/indicators",
    operation_id="listIndicators",
    tags=["indicators"],
    summary="Latest strategic indicators of a domain, with change vs the previous periode",
    response_model=IndicatorList,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown domain"}},
)
def list_indicators(
    conn: Conn,
    domain: Annotated[
        str,
        Query(pattern=r"^\d{4}$", description="National `0000` (default) or a province domain."),
    ] = "0000",
) -> IndicatorList:
    """Every indicator's latest snapshot (``v_indicator_latest``) plus the previous periode's
    value and the change (``null`` when unknown); ``variable`` links to the Explorer when the
    underlying variable is crawled. A known domain without indicators → empty ``items``."""
    row = domain_row(conn, domain)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found")
    return IndicatorList.model_validate({"domain": row, "items": load_indicators(conn, domain)})


@router.get(
    "/indicators/{domain}/{indicator_id}/history",
    operation_id="getIndicatorHistory",
    tags=["indicators"],
    summary="Every recorded periode of one strategic indicator",
    response_model=IndicatorHistory,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown indicator"}},
)
def get_indicator_history(
    conn: Conn,
    domain: DomainId,
    indicator_id: Annotated[int, Path(description="BPS indicator id within the domain.")],
) -> IndicatorHistory:
    """The latest snapshot (as in ``GET /indicators``) plus one point per periode seen by the
    crawler, oldest first (sighting order: ``periode`` is free text)."""
    history = load_history(conn, domain, indicator_id)
    if history is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Indicator not found")
    return IndicatorHistory.model_validate(history)


# --- trade (U6) ----------------------------------------------------------------------------------

_RANGE_DOC = (
    "`YYYY` or `YYYY-MM`. Two years → whole years (annual figures, else the sum of the year's "
    "months); otherwise months. One of `from`/`to` alone means both."
)
From = Annotated[
    str | None,
    Query(alias="from", pattern=trade.PERIOD_PATTERN, description=f"Range start: {_RANGE_DOC}"),
]
To = Annotated[
    str | None,
    Query(alias="to", pattern=trade.PERIOD_PATTERN, description="Range end (inclusive)."),
]


def _range(start: str | None, end: str | None) -> trade.PeriodRange | None:
    if start is None and end is None:
        return None
    try:
        return trade.parse_range(start, end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


def _flows(flow: TradeFlow | None) -> list[int]:
    return list(trade.FLOWS.values()) if flow is None else [trade.FLOWS[flow]]


@router.get(
    "/trade/periods",
    operation_id="getTradePeriods",
    tags=["trade"],
    summary="Years and months with trade data, per flow",
    response_model=TradePeriods,
)
def get_trade_periods(conn: Conn) -> TradePeriods:
    """Every (flow, year) with data: whether BPS annual figures exist and which months have
    monthly figures; plus the latest year and month (dashboard defaults)."""
    return TradePeriods.model_validate(trade.load_periods(conn))


@router.get(
    "/trade/summary",
    operation_id="getTradeSummary",
    tags=["trade"],
    summary="Total exports / imports (value, net weight) and the trade balance",
    response_model=TradeSummary,
)
def get_trade_summary(
    conn: Conn,
    flow: Annotated[TradeFlow | None, Query(description="One flow; default: both.")] = None,
    year: Annotated[
        int | None,
        Query(ge=2000, le=2100, description="A whole year (shorthand for from=to=YYYY)."),
    ] = None,
    month: Annotated[
        int | None, Query(ge=1, le=12, description="With `year`: that one month.")
    ] = None,
    start: From = None,
    end: To = None,
) -> TradeSummary:
    """Totals over one period (`year`, `year`+`month`) or a range (`from`/`to`); nothing given →
    the latest year with data. A year counts its annual figures, or — without them — the sum
    of its monthly figures (year to date); `periods` says which, per year."""
    if month is not None and year is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "`month` needs `year`")
    if year is not None and (start is not None or end is not None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "give `year`/`month` or `from`/`to`, not both"
        )
    if year is not None:
        rng: trade.PeriodRange | None = (
            trade.year_range(year) if month is None else trade.month_range(year, month)
        )
    else:
        rng = _range(start, end)
    return TradeSummary.model_validate(trade.load_summary(conn, _flows(flow), rng))


@router.get(
    "/trade/breakdown",
    operation_id="getTradeBreakdown",
    tags=["trade"],
    summary="Top N countries, ports or HS chapters by value, plus an others bucket",
    response_model=TradeBreakdown,
)
def get_trade_breakdown(
    conn: Conn,
    by: Annotated[TradeBy, Query(description="Group by HS chapter, country or port.")],
    flow: Annotated[TradeFlow, Query()] = "export",
    start: From = None,
    end: To = None,
    top: Annotated[int, Query(ge=1, le=50, description="Items to list before `others`.")] = 10,
) -> TradeBreakdown:
    """The flow's value and net weight per chapter / country / port over the range (default:
    the latest year with data), largest first; the rest summed into `others`. Chapters are
    labelled with their HS description."""
    return TradeBreakdown.model_validate(
        trade.load_breakdown(conn, by, trade.FLOWS[flow], _range(start, end), top)
    )


@router.get(
    "/trade/series",
    operation_id="getTradeSeries",
    tags=["trade"],
    summary="Monthly trade values, optionally for one HS chapter and/or country",
    response_model=TradeSeriesResponse,
)
def get_trade_series(
    conn: Conn,
    hs2: Annotated[
        str | None, Query(pattern=r"^\d{2}$", description="HS chapter, e.g. `27`.")
    ] = None,
    country: Annotated[
        str | None, Query(max_length=200, description="Country name as BPS writes it.")
    ] = None,
    flow: Annotated[
        TradeFlow | None, Query(description="One flow; default: both, plus the trade balance.")
    ] = None,
    start: From = None,
    end: To = None,
) -> TradeSeriesResponse:
    """One series of monthly figures per flow (default: all months with data), and with both
    flows the monthly balance (exports - imports) where both have data."""
    return TradeSeriesResponse.model_validate(
        trade.load_series(conn, _flows(flow), hs2=hs2, country=country, rng=_range(start, end))
    )
