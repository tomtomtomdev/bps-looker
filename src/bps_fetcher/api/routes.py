"""Endpoints. Every route has an explicit ``operation_id`` and tag: the U1 TypeScript client is
generated from ``web/openapi.json`` and its function names must not drift."""

import logging
from typing import Annotated

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError

from bps_fetcher.api.deps import Conn, get_engine
from bps_fetcher.api.models import (
    Domain,
    DomainLevel,
    Health,
    SeriesResponse,
    VariableDetail,
    VariablePage,
    VariableSummary,
)
from bps_fetcher.api.search import search_query
from bps_fetcher.api.series import MAX_SERIES, dimensions, load_series, variable_row
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
