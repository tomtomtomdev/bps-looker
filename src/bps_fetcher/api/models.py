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
