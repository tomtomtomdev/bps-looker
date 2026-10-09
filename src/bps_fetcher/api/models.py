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
