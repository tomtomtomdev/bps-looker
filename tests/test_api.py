"""U0: read API scaffold — health, domains, OpenAPI, CORS, ``bps serve`` / ``bps openapi``
(DB tests need Postgres)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import InternalError
from typer.testing import CliRunner

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.models import DomainLevel
from bps_fetcher.cli import app as cli_app
from bps_fetcher.cli import serve_app
from bps_fetcher.db.schema import DOMAIN_LEVELS
from bps_fetcher.settings import ApiSettings

WEB_ORIGIN = "http://localhost:3000"
OPENAPI_FILE = Path(__file__).parents[1] / "web" / "openapi.json"
runner = CliRunner()


def _settings(url: str, origins: str = WEB_ORIGIN) -> ApiSettings:
    return ApiSettings(_env_file=None, database_url=url, web_origin=origins)


@pytest.fixture
def api(db_engine: Engine) -> FastAPI:
    return create_app(_settings(db_engine.url.render_as_string(hide_password=False)))


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c,
    ):
        yield c


def _seed_domains(engine: Engine) -> None:
    rows = [
        ("0000", "Indonesia", "https://www.bps.go.id", "pusat"),
        ("1100", "Aceh", "https://aceh.bps.go.id", "prov"),
        ("3100", "DKI Jakarta", "https://jakarta.bps.go.id", "prov"),
        ("1101", "Simeulue", None, "kab"),
    ]
    with engine.begin() as conn:
        for domain_id, name, url, level in rows:
            conn.execute(
                text("INSERT INTO domain (domain_id, name, url, level) VALUES (:d, :n, :u, :l)"),
                {"d": domain_id, "n": name, "u": url, "l": level},
            )


# --- settings -----------------------------------------------------------------------------------


def test_api_settings_need_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BPS_API_KEY", raising=False)
    monkeypatch.delenv("BPS_WEB_ORIGIN", raising=False)
    s = ApiSettings(_env_file=None)
    assert s.web_origins == ["http://localhost:3000"]


def test_api_settings_origin_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_WEB_ORIGIN", " https://a.example , https://b.example/,")
    assert ApiSettings(_env_file=None).web_origins == ["https://a.example", "https://b.example"]


# --- health -------------------------------------------------------------------------------------


async def test_health_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["revision"]


async def test_health_db_unreachable() -> None:
    api = create_app(_settings("postgresql+psycopg://bps:bps@127.0.0.1:1/nope?connect_timeout=1"))
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c,
    ):
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "database": "unreachable", "revision": None}


# --- domains ------------------------------------------------------------------------------------


async def test_domains_by_level(client: httpx.AsyncClient, db_engine: Engine) -> None:
    _seed_domains(db_engine)
    r = await client.get("/domains", params={"level": "prov"})
    assert r.status_code == 200
    assert r.json() == [
        {"domain_id": "1100", "name": "Aceh", "url": "https://aceh.bps.go.id", "level": "prov"},
        {
            "domain_id": "3100",
            "name": "DKI Jakarta",
            "url": "https://jakarta.bps.go.id",
            "level": "prov",
        },
    ]


async def test_domains_all(client: httpx.AsyncClient, db_engine: Engine) -> None:
    _seed_domains(db_engine)
    r = await client.get("/domains")
    assert r.status_code == 200
    assert [d["domain_id"] for d in r.json()] == ["0000", "1100", "1101", "3100"]


async def test_domains_bad_level(client: httpx.AsyncClient) -> None:
    r = await client.get("/domains", params={"level": "city"})
    assert r.status_code == 422


# --- CORS ---------------------------------------------------------------------------------------


async def test_cors_allows_web_origin(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/domains",
        headers={"Origin": WEB_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == WEB_ORIGIN
    r = await client.get("/health", headers={"Origin": WEB_ORIGIN})
    assert r.headers["access-control-allow-origin"] == WEB_ORIGIN


async def test_cors_rejects_other_origin(client: httpx.AsyncClient) -> None:
    evil = "https://evil.example"
    r = await client.options(
        "/domains", headers={"Origin": evil, "Access-Control-Request-Method": "GET"}
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r = await client.get("/health", headers={"Origin": evil})
    assert "access-control-allow-origin" not in r.headers


# --- OpenAPI ------------------------------------------------------------------------------------


def test_openapi_operation_ids_and_tags() -> None:
    schema = openapi_schema()
    ops = {
        (path, method): op for path, item in schema["paths"].items() for method, op in item.items()
    }
    assert ops[("/health", "get")]["operationId"] == "getHealth"
    assert ops[("/health", "get")]["tags"] == ["meta"]
    assert ops[("/domains", "get")]["operationId"] == "listDomains"
    assert ops[("/domains", "get")]["tags"] == ["domains"]
    level = ops[("/domains", "get")]["parameters"][0]
    assert level["name"] == "level"
    assert "Domain" in schema["components"]["schemas"]
    assert "Health" in schema["components"]["schemas"]


async def test_openapi_served(client: httpx.AsyncClient) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["paths"].keys() == openapi_schema()["paths"].keys()


def test_committed_openapi_is_current() -> None:
    """``web/openapi.json`` (the U1 TS client's input) must match the app — run `make openapi`."""
    committed: dict[str, Any] = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert committed == openapi_schema()


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_openapi_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BPS_API_KEY", raising=False)
    out = tmp_path / "sub" / "openapi.json"
    result = runner.invoke(cli_app, ["openapi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8")) == openapi_schema()
    assert out.read_text(encoding="utf-8").endswith("}\n")


def test_cli_serve_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BPS_API_KEY", raising=False)
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.append((app, kw)))
    result = runner.invoke(cli_app, ["serve", "--host", "0.0.0.0", "--port", "8123"])
    assert result.exit_code == 0, result.output
    [(served, kwargs)] = calls
    # An import string + factory (each worker process builds its own app).
    assert served == "bps_fetcher.cli:serve_app"
    assert kwargs["factory"] is True
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8123
    assert kwargs["workers"] == 1
    assert isinstance(serve_app(), FastAPI)


def test_cli_serve_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.append(kw))
    assert runner.invoke(cli_app, ["serve", "--workers", "3"]).exit_code == 0
    monkeypatch.setenv("BPS_API_WORKERS", "2")
    assert runner.invoke(cli_app, ["serve"]).exit_code == 0
    assert [kw["workers"] for kw in calls] == [3, 2]


async def test_connections_are_read_only(client: httpx.AsyncClient, api: FastAPI) -> None:
    with api.state.engine.connect() as conn, pytest.raises(InternalError, match="read-only"):
        conn.execute(text("INSERT INTO domain (domain_id, name, level) VALUES ('9', 'x', 'kab')"))


def test_domain_level_matches_schema() -> None:
    assert get_args(DomainLevel) == DOMAIN_LEVELS
