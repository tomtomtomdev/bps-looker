"""Endpoints. Every route has an explicit ``operation_id`` and tag: the U1 TypeScript client is
generated from ``web/openapi.json`` and its function names must not drift."""

import logging
from typing import Annotated

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError

from bps_fetcher.api.deps import Conn, get_engine
from bps_fetcher.api.models import Domain, DomainLevel, Health
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
