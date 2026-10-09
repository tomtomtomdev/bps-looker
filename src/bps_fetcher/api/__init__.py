"""Read-only HTTP API over the crawled data (FastAPI) — the backend of the web UI (U0-U7)."""

from bps_fetcher.api.app import create_app, openapi_schema

__all__ = ["create_app", "openapi_schema"]
