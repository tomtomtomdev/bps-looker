"""Pydantic response models — they define the OpenAPI schema the web client is generated from."""

import datetime as dt
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DomainLevel = Literal["pusat", "prov", "kab"]


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unreachable"]
    revision: str | None = None
    """Alembic revision of the database (``None`` when unreachable or never migrated)."""


class Domain(BaseModel):
    domain_id: str
    name: str
    url: str | None = None
    level: DomainLevel


class VariableSummary(BaseModel):
    """A dynamic-table variable as listed by search (no definition/notes — see U3 detail)."""

    domain_id: str
    domain_name: str
    domain_level: DomainLevel
    var_id: int
    title: str
    unit: str | None = None
    subject_id: int | None = None
    """BPS subject (``sub_id``) — the ``subject`` filter of ``GET /variables``."""
    subject: str | None = None
    """Subject name (``sub_name``), e.g. ``Inflasi``."""
    category: str | None = None
    """Subject category (``subcsa_name``), e.g. ``Harga-Harga``."""


class VariablePage(BaseModel):
    items: list[VariableSummary]
    total: int
    """Matching variables across all pages."""
    page: int
    page_size: int


Freq = Literal["month", "quarter", "semester", "year", "other"]
"""Sub-period kind of a ``turth`` member (see ``api/series.py`` for the period → date rules)."""


class VervarMember(BaseModel):
    """A vertical-variable member (usually a region or a category)."""

    val: int
    label: str
    group_label: str | None = None


class DimMember(BaseModel):
    val: int
    label: str


class TurthMember(BaseModel):
    """A sub-period member (month, quarter, …, or the annual total)."""

    val: int
    label: str
    freq: Freq
    has_data: bool
    """Whether any observation uses it (monthly vars list ``13 = Tahunan`` but have no values)."""


class PeriodMember(BaseModel):
    th: int
    label: str
    """Usually the year, e.g. ``2024``."""


class VariableDetail(VariableSummary):
    """A variable's metadata and dimensions. ``definition``/``notes`` are BPS HTML: sanitize
    before rendering."""

    definition: str | None = None
    notes: str | None = None
    decimal: int | None = None
    """Decimal places BPS shows values with."""
    last_update: datetime | None = None
    """BPS's last-update stamp of the data (local time, no zone)."""
    vervars: list[VervarMember]
    turvars: list[DimMember]
    turths: list[TurthMember]
    periods: list[PeriodMember]


class SeriesPoint(BaseModel):
    period: str
    """``YYYY-MM`` (month), ``YYYY-Qn``, ``YYYY-Sn``, ``YYYY`` (annual), else ``<year> <label>``."""
    date: dt.date | None
    """Start of the period (semesters: their reference month); ``None`` when unknown."""
    th: int
    turth: int
    value: float


class Series(BaseModel):
    vervar: int
    vervar_label: str | None
    turvar: int
    turvar_label: str | None
    points: list[SeriesPoint]
    """Sorted by time."""


class SeriesResponse(BaseModel):
    series: list[Series]
    truncated: bool
    """More vervar x turvar combinations were requested than ``max_series``."""
    max_series: int


class CrossSectionPeriod(BaseModel):
    th: int
    turth: int
    period: str
    """Same format as ``SeriesPoint.period`` (``2024-03``, ``2024-Q2``, ``2024``)."""
    date: dt.date | None
    label: str
    """Human label: ``Maret 2024``, ``Triwulan II 2024``, ``2024``."""


class CrossSectionRegion(BaseModel):
    vervar: int
    """Region code (provinces ``xx00``, regencies ``xxyy``) or another vervar member."""
    label: str | None
    """BPS label as stored (may contain HTML such as ``<b>ACEH</b>``)."""
    value: float | None
    """``None`` when the region has no value for the period."""


class CrossSection(BaseModel):
    """Every vervar member's value for one period: ranking + map."""

    turvar: int | None
    turvar_label: str | None
    period: CrossSectionPeriod | None
    """The period shown (``None`` when nothing matches the request)."""
    periods: list[CrossSectionPeriod]
    """Periods with data for this turvar (and ``freq``), in time order — the period slider."""
    regions: list[CrossSectionRegion]
    """Sorted by value, highest first; members without a value last. Excludes the national
    aggregate."""
    national: CrossSectionRegion | None
    """The national aggregate (vervar 9999 / ``INDONESIA``), if the variable has one."""


class VariableRef(BaseModel):
    """A crawled dynamic-table variable (link target in the Explorer)."""

    domain_id: str
    var_id: int
    title: str


class IndicatorPrevious(BaseModel):
    periode: str
    value: float | None
    """``None`` when BPS published a non-numeric value."""


class Indicator(BaseModel):
    """A strategic indicator's latest snapshot (``v_indicator_latest``) and its change vs the
    previous periode (periodes are ordered by when they were seen — see ``api/indicators.py``)."""

    domain_id: str
    indicator_id: int
    title: str
    """BPS title, usually ending in the period: ``Inflasi Year on Year, September 2026``."""
    label: str
    """The title without its trailing period: ``Inflasi Year on Year``."""
    name: str | None = None
    """BPS's longer description (may be a sentence or source notes)."""
    value: float | None
    """``None`` when BPS published a non-numeric value."""
    unit: str | None = None
    periode: str
    """Free text: ``September 2026``, ``Triwulan II 2026``, ``Semester 1 (Maret) 2026``."""
    category: int | None = None
    subject_csa: int | None = None
    data_source: str | None = None
    first_seen: datetime
    last_seen: datetime
    var: int | None = None
    """BPS's underlying dynamic-table variable id."""
    variable: VariableRef | None
    """That variable when it is crawled in the same domain (Explorer link), else ``None``."""
    previous: IndicatorPrevious | None
    """The periode seen before the latest one; ``None`` with a single periode."""
    change: float | None
    """``value - previous.value`` (``None`` unless both are numbers)."""
    change_pct: float | None
    """Change in % of ``|previous.value|`` (``None`` also when the previous value is 0)."""


class IndicatorList(BaseModel):
    domain: Domain
    items: list[Indicator]
    """Ordered by ``indicator_id``."""


class IndicatorPoint(BaseModel):
    periode: str
    title: str
    value: float | None
    first_seen: datetime
    """Earliest sighting of this periode."""
    last_seen: datetime


class IndicatorHistory(Indicator):
    points: list[IndicatorPoint]
    """One per periode, oldest first; the last is the latest snapshot."""


# --- trade (U6) ----------------------------------------------------------------------------------

TradeFlow = Literal["export", "import"]
TradeBy = Literal["hs2", "country", "port"]


class TradeYear(BaseModel):
    """Trade data available for one flow and year."""

    flow: TradeFlow
    year: int
    annual: bool
    """BPS published annual figures (for the current year: year to date)."""
    months: list[int]
    """Months with monthly figures."""


class TradePeriods(BaseModel):
    items: list[TradeYear]
    latest_year: int | None = None
    latest_month: str | None = None
    """Latest month with monthly figures, ``YYYY-MM``."""


class TradeRange(BaseModel):
    """The resolved period range: ``year`` granularity (``2024``…``2025``) or ``month``
    (``2024-11``…``2025-02``)."""

    start: str
    end: str
    granularity: Literal["year", "month"]


class TradeYearBasis(BaseModel):
    """How a year of the range was counted: its ``annual`` rows, or the sum of its ``monthly``
    rows (a partial year when ``months`` < 12)."""

    year: int
    basis: Literal["annual", "monthly"]
    months: int | None = None
    """Months with monthly figures counted (monthly basis) / available (annual basis)."""


class TradeTotal(BaseModel):
    flow: TradeFlow
    value_usd: float | None = None
    """``null`` when the range has no data."""
    netweight_kg: float | None = None
    periods: list[TradeYearBasis]


class TradeSummary(BaseModel):
    range: TradeRange | None = None
    """``null`` only when there is no trade data at all."""
    items: list[TradeTotal]
    balance_usd: float | None = None
    """Exports - imports, when both flows were asked for and have data."""


class TradeBreakdownItem(BaseModel):
    key: str
    """HS chapter (``27``), country or port; ``''`` = not stated by BPS."""
    label: str | None = None
    """Chapter description; the country / port name (``null`` for ``''``)."""
    value_usd: float | None = None
    netweight_kg: float | None = None
    share: float | None = None
    """Fraction (0-1) of the range's total value."""


class TradeOthers(BaseModel):
    """Everything below the top N, as one bucket."""

    count: int
    value_usd: float | None = None
    netweight_kg: float | None = None
    share: float | None = None


class TradeBreakdown(BaseModel):
    by: TradeBy
    flow: TradeFlow
    top: int
    range: TradeRange | None = None
    total: TradeTotal
    items: list[TradeBreakdownItem]
    others: TradeOthers | None = None


class TradePoint(BaseModel):
    period: str
    """``YYYY-MM``."""
    date: dt.date
    value_usd: float | None = None
    netweight_kg: float | None = None


class TradeSeries(BaseModel):
    flow: TradeFlow
    points: list[TradePoint]


class TradeBalancePoint(BaseModel):
    period: str
    date: dt.date
    value_usd: float
    """Exports - imports."""


class TradeSeriesResponse(BaseModel):
    hs2: str | None = None
    hs2_label: str | None = None
    country: str | None = None
    range: TradeRange | None = None
    series: list[TradeSeries]
    balance: list[TradeBalancePoint]
    """Months where both flows have data (only when both were asked for)."""
