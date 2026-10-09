"""Endpoints. Every route has an explicit ``operation_id`` and tag: the U1 TypeScript client is
generated from ``web/openapi.json`` and its function names must not drift."""

import logging
from typing import Annotated

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError

from bps_fetcher.api.deps import Conn, get_engine
from bps_fetcher.api.models import Domain, DomainLevel, Health, VariablePage, VariableSummary
from bps_fetcher.api.search import search_query
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
