"""Pydantic response models — they define the OpenAPI schema the web client is generated from."""

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
