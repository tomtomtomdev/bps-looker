"""App factory. ``create_app(settings)`` builds the FastAPI app; the SQLAlchemy engine is created
in the lifespan (so building the app — e.g. for the OpenAPI schema — never touches the DB) and
runs every transaction READ ONLY: the API can't write even if a bug tried to."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from sqlalchemy import create_engine

from bps_fetcher import __version__
from bps_fetcher.api.routes import router
from bps_fetcher.settings import ApiSettings

TITLE = "BPS looker read API"

OPENAPI_TAGS = [
    {"name": "meta", "description": "Service health."},
    {"name": "domains", "description": "BPS domains: national (pusat), provinces, regencies."},
    {"name": "variables", "description": "Dynamic-table variables: search, metadata, data."},
]


def _operation_id(route: APIRoute) -> str:
    # Every route sets operation_id explicitly; this is only the fallback for one that doesn't.
    return route.name


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            execution_options={"postgresql_readonly": True},
        )
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title=TITLE,
        version=__version__,
        description="Read-only access to the crawled BPS data (labeled `v_*` views).",
        openapi_tags=OPENAPI_TAGS,
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


def openapi_schema() -> dict[str, Any]:
    """The OpenAPI document (independent of env: settings only affect DB/CORS, not the schema)."""
    return create_app(ApiSettings(_env_file=None)).openapi()
