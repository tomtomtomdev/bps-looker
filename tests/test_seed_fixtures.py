"""U7: ``bps seed-fixtures`` — a deterministic DB for the stack e2e, built by running the real
handlers through the queue + worker over the recorded fixtures (no BPS calls). DB tests need
Postgres."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, func, select, text
from typer.testing import CliRunner

from bps_fetcher.api import create_app
from bps_fetcher.cli import app as cli_app
from bps_fetcher.db.schema import (
    domain,
    indicator_snapshot,
    observation,
    period,
    task,
    trade_flow,
    variable,
)
from bps_fetcher.paginate import NOT_AVAILABLE
from bps_fetcher.seed_fixtures import FixtureClient, MissingFixtureError, seed_fixtures
from bps_fetcher.settings import ApiSettings
from conftest import FIXTURES_DIR


async def test_client_serves_recorded_bodies_by_path_and_params() -> None:
    client = FixtureClient.from_dir(FIXTURES_DIR)
    body = await client.get("list", model="data", domain="0000", var=2263, th="124")
    assert body["var"][0]["val"] == 2263
    trade = await client.get("dataexim/", sumber=1, periode=2, kodehs="03", jenishs=1, tahun=2024)
    assert trade["status"] == "OK"
    assert client.served == {"data_0000_2263", "trade_exp_annual_03_2024"}


async def test_client_answers_unrecorded_requests_not_available() -> None:
    client = FixtureClient.from_dir(FIXTURES_DIR)
    body = await client.get("list", model="var", domain="0000", page=2)
    assert body["data-availability"] == NOT_AVAILABLE
    assert client.misses == [("list", {"model": "var", "domain": "0000", "page": 2})]


def test_client_ignores_error_fixtures_and_needs_the_seed_set(tmp_path: Any) -> None:
    client = FixtureClient.from_dir(FIXTURES_DIR)
    assert not any(name.startswith("error_") for name in client.names)
    with pytest.raises(MissingFixtureError, match="domain_all"):
        FixtureClient.from_dir(tmp_path)


def _counts(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:

        def n(t: Any) -> int:
            return int(conn.execute(select(func.count()).select_from(t)).scalar_one())

        statuses: dict[str, int] = dict(
            conn.execute(select(task.c.status, func.count()).group_by(task.c.status)).all()
        )
        rollup = conn.execute(text("SELECT count(*) FROM mv_trade_rollup")).scalar_one()
        return {
            "domain": n(domain),
            "variable": n(variable),
            "period": n(period),
            "observation": n(observation),
            "indicator_snapshot": n(indicator_snapshot),
            "trade_flow": n(trade_flow),
            "rollup": rollup,
            "tasks": statuses,
        }


def test_seed_fixtures_loads_every_source(db_engine: Engine) -> None:
    result = seed_fixtures(db_engine, FIXTURES_DIR)

    assert result.failed == 0
    assert result.unused == []  # every seed fixture was requested by some handler
    counts = _counts(db_engine)
    assert counts["tasks"].keys() == {"done"}
    assert counts["domain"] > 500
    with db_engine.connect() as conn:
        titles = dict(
            conn.execute(
                select(variable.c.var_id, variable.c.title).where(
                    variable.c.var_id.in_([1804, 2263])
                )
            ).all()
        )
    assert titles[2263].startswith("Inflasi Tahunan")
    assert 1804 in titles
    assert counts["variable"] >= 12  # var list page 1 (10) + the two data variables
    assert counts["period"] >= 7  # th list of 1804
    assert counts["observation"] > 400  # 2263: 39 regions x 12 months; 1804: 3 years
    assert counts["indicator_snapshot"] == 16
    assert counts["trade_flow"] > 0
    assert counts["rollup"] > 0  # refreshed after the trade tasks

    again = seed_fixtures(db_engine, FIXTURES_DIR)  # idempotent
    assert again.failed == 0
    assert _counts(db_engine) == counts


@pytest.fixture
def seeded_engine(db_engine: Engine) -> Engine:
    seed_fixtures(db_engine, FIXTURES_DIR)  # sync: runs its own event loop
    return db_engine


@pytest.fixture
async def seeded_client(seeded_engine: Engine) -> AsyncIterator[httpx.AsyncClient]:
    db_engine = seeded_engine
    url = db_engine.url.render_as_string(hide_password=False)
    api: FastAPI = create_app(ApiSettings(_env_file=None, database_url=url))
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c,
    ):
        yield c


async def test_seeded_db_serves_what_the_stack_smoke_needs(
    seeded_client: httpx.AsyncClient,
) -> None:
    """The UI paths the Playwright stack smoke walks have data behind them."""
    found = (await seeded_client.get("/variables", params={"q": "inflasi"})).json()
    assert [v["var_id"] for v in found["items"]] == [2263]

    detail = (await seeded_client.get("/variables/0000/2263")).json()
    assert len(detail["vervars"]) == 39
    cross = await seeded_client.get("/variables/0000/2263/cross-section")
    assert cross.status_code == 200
    assert len(cross.json()["regions"]) >= 38

    indicators = (await seeded_client.get("/indicators")).json()["items"]
    inflation = next(i for i in indicators if i["title"].startswith("Inflasi Year on Year"))
    history = await seeded_client.get(f"/indicators/0000/{inflation['indicator_id']}/history")
    assert history.status_code == 200

    summary = (await seeded_client.get("/trade/summary")).json()
    assert summary["range"]["start"] == "2024"
    exports = next(i for i in summary["items"] if i["flow"] == "export")
    assert float(exports["value_usd"]) > 0
    breakdown = (
        await seeded_client.get("/trade/breakdown", params={"by": "hs2", "flow": "export"})
    ).json()
    assert [i["key"] for i in breakdown["items"]] == ["03"]
    series = (await seeded_client.get("/trade/series")).json()
    assert any(s["points"] for s in series["series"])


def test_cli_seed_fixtures(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    result = CliRunner().invoke(cli_app, ["seed-fixtures", str(FIXTURES_DIR)])
    assert result.exit_code == 0, result.output
    assert "Seeded from fixtures" in result.output
    assert _counts(db_engine)["indicator_snapshot"] == 16


def test_cli_seed_fixtures_missing_dir(db_engine: Engine, tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    result = CliRunner().invoke(cli_app, ["seed-fixtures", str(tmp_path)])
    assert result.exit_code == 1
    assert "domain_all" in result.output
