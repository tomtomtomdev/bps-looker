"""S13: ``bps`` CLI — seed/work/status/migrate, end to end against a respx-mocked API."""

import copy
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select, text
from typer.testing import CliRunner

from bps_fetcher.cli import app
from bps_fetcher.client import BASE_URL
from bps_fetcher.db.schema import (
    dim_turth,
    dim_turvar,
    dim_vervar,
    domain,
    observation,
    period,
    raw_response,
    task,
    variable,
)
from bps_fetcher.settings import get_settings

runner = CliRunner()

NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}
FAKE_KEY = "test-key-0123456789abcdef"


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, db_url: str) -> Iterator[None]:
    """Point the CLI at the test DB with a fake key and no rate limiting."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BPS_RPS", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _count(engine: Engine, table: Any) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


# --- help ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["seed"],
        ["seed", "dynamic"],
        ["seed", "indicators"],
        ["seed", "trade"],
        ["work"],
        ["status"],
        ["migrate"],
    ],
)
def test_help_works_for_every_command(args: list[str]) -> None:
    result = runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_console_script_declared() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["bps"] == "bps_fetcher.cli:main"


# --- migrations ----------------------------------------------------------------------------------


def test_unmigrated_db_gives_clear_error(cli_env: None, empty_db: str) -> None:
    for args in (["seed", "dynamic", "--domain", "0000"], ["work", "--drain"], ["status"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, result.output
        assert "bps migrate" in result.output


def test_migrate_then_status(cli_env: None, empty_db: str) -> None:
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "no tasks" in result.output.lower()


# --- seed ----------------------------------------------------------------------------------------


def test_seed_dynamic_enqueues_one_restricted_domains_task(
    cli_env: None, db_engine: Engine
) -> None:
    result = runner.invoke(app, ["seed", "dynamic", "--domain", "0000", "--limit-vars", "5"])
    assert result.exit_code == 0, result.output
    with db_engine.connect() as conn:
        rows = conn.execute(select(task.c.kind, task.c.params, task.c.status)).all()
    assert [tuple(r) for r in rows] == [
        ("domains", {"type": "all", "domains": ["0000"], "limit_vars": 5}, "pending")
    ]

    again = runner.invoke(app, ["seed", "dynamic", "--domain", "0000", "--limit-vars", "5"])
    assert again.exit_code == 0, again.output
    assert "already" in again.output.lower()
    assert _count(db_engine, task) == 1


def test_seed_dynamic_level_and_validation(cli_env: None, db_engine: Engine) -> None:
    result = runner.invoke(app, ["seed", "dynamic", "--level", "prov"])
    assert result.exit_code == 0, result.output
    with db_engine.connect() as conn:
        params = conn.execute(select(task.c.params)).scalar_one()
    assert params == {"type": "all", "level": ["prov"]}

    assert runner.invoke(app, ["seed", "dynamic", "--domain", "12"]).exit_code != 0
    assert runner.invoke(app, ["seed", "dynamic", "--level", "desa"]).exit_code != 0
    assert runner.invoke(app, ["seed", "dynamic", "--limit-vars", "0"]).exit_code != 0


def _add_domains(engine: Engine, *ids: str) -> None:
    from bps_fetcher.handlers.domains import domain_level

    with engine.begin() as conn:
        conn.execute(
            domain.insert(),
            [{"domain_id": d, "name": f"D{d}", "level": domain_level(d)} for d in ids],
        )


def test_seed_indicators_all_pusat_and_prov_domains(cli_env: None, db_engine: Engine) -> None:
    _add_domains(db_engine, "0000", "1100", "1101")
    result = runner.invoke(app, ["seed", "indicators"])
    assert result.exit_code == 0, result.output
    assert "2" in result.output
    with db_engine.connect() as conn:
        rows = conn.execute(select(task.c.kind, task.c.params).order_by(task.c.id)).all()
    assert [tuple(r) for r in rows] == [
        ("indicators", {"domain": "0000"}),
        ("indicators", {"domain": "1100"}),
    ]


def test_seed_indicators_domain_and_run(cli_env: None, db_engine: Engine) -> None:
    result = runner.invoke(app, ["seed", "indicators", "--domain", "0000", "--run", "2026-10-09"])
    assert result.exit_code == 0, result.output
    with db_engine.connect() as conn:
        params = conn.execute(select(task.c.params)).scalar_one()
    assert params == {"domain": "0000", "run": "2026-10-09"}

    again = runner.invoke(app, ["seed", "indicators", "--domain", "0000", "--run", "2026-10-09"])
    assert again.exit_code == 0, again.output
    assert "0" in again.output
    assert _count(db_engine, task) == 1


def test_seed_indicators_rejects_kab_domain(cli_env: None, db_engine: Engine) -> None:
    result = runner.invoke(app, ["seed", "indicators", "--domain", "0000", "--domain", "1101"])
    assert result.exit_code != 0
    assert "1101" in result.output
    assert _count(db_engine, task) == 0


def test_seed_indicators_empty_domain_table_hints_seed_dynamic(
    cli_env: None, db_engine: Engine
) -> None:
    result = runner.invoke(app, ["seed", "indicators"])
    assert result.exit_code != 0
    assert "bps seed dynamic" in result.output
    assert _count(db_engine, task) == 0


def test_seed_trade_one_flow_period_year(cli_env: None, db_engine: Engine) -> None:
    args = ["seed", "trade", "--from", "2024", "--to", "2024", "--flow", "exp"]
    result = runner.invoke(app, [*args, "--period", "annual", "--batch-size", "50"])
    assert result.exit_code == 0, result.output
    assert "2" in result.output
    with db_engine.connect() as conn:
        rows = conn.execute(select(task.c.kind, task.c.params).order_by(task.c.id)).all()
    assert [r.kind for r in rows] == ["trade", "trade"]
    assert [(r.params["flow"], r.params["period_type"], r.params["year"]) for r in rows] == [
        (1, 2, 2024),
        (1, 2, 2024),
    ]
    assert rows[0].params["chapters"].startswith("01;02;")
    assert "run" not in rows[0].params

    again = runner.invoke(app, [*args, "--period", "annual", "--batch-size", "50"])
    assert again.exit_code == 0, again.output
    assert _count(db_engine, task) == 2


def test_seed_trade_defaults_both_flows_and_periods(cli_env: None, db_engine: Engine) -> None:
    result = runner.invoke(app, ["seed", "trade", "--from", "2023", "--to", "2024", "--run", "r1"])
    assert result.exit_code == 0, result.output
    with db_engine.connect() as conn:
        params = conn.execute(select(task.c.params)).scalars().all()
    # 2 flows x 2 period types x 2 years x 10 batches of 10 chapters (98 chapters)
    assert len(params) == 80
    assert {(p["flow"], p["period_type"]) for p in params} == {(1, 1), (1, 2), (2, 1), (2, 2)}
    assert {p["run"] for p in params} == {"r1"}


def test_seed_trade_to_defaults_to_current_year(cli_env: None, db_engine: Engine) -> None:
    from datetime import date

    year = date.today().year
    args = ["seed", "trade", "--from", str(year), "--flow", "imp", "--period", "monthly"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    with db_engine.connect() as conn:
        years = set(conn.execute(select(task.c.params["year"].as_integer())).scalars())
    assert years == {year}


@pytest.mark.parametrize(
    "args",
    [
        ["--from", "2013"],
        ["--from", "2024", "--to", "2023"],
        ["--from", "2024", "--flow", "both"],
        ["--from", "2024", "--period", "weekly"],
        ["--from", "2024", "--batch-size", "0"],
    ],
)
def test_seed_trade_rejects_bad_options(cli_env: None, db_engine: Engine, args: list[str]) -> None:
    result = runner.invoke(app, ["seed", "trade", *args])
    assert result.exit_code != 0, result.output
    assert _count(db_engine, task) == 0


# --- end to end ----------------------------------------------------------------------------------


class FakeApi:
    """Serves recorded fixtures for /domain and /list (var, th, data); records requests."""

    def __init__(self, fixture_body: Callable[[str], Any]) -> None:
        self.calls: list[dict[str, str]] = []
        self.domains = fixture_body("domain_all")
        var_page = copy.deepcopy(fixture_body("var_0000_p1"))
        items = var_page["data"][1]
        # Put the two vars we have data fixtures for first; the listing still claims 176 pages.
        items[0]["var_id"], items[1]["var_id"] = 1804, 2263
        self.var_ids = [i["var_id"] for i in items]
        self.var_page = var_page
        th_1804 = copy.deepcopy(fixture_body("th_0000_1804"))
        th_1804["data"][1] = [i for i in th_1804["data"][1] if 117 <= i["th_id"] <= 119]
        th_1804["data"][0].update(count=3, total=3)
        self.th = {
            "1804": th_1804,
            "2263": {
                "status": "OK",
                "data-availability": "available",
                "data": [
                    {"page": 1, "pages": 1, "count": 1, "total": 1},
                    [{"th_id": 124, "th": "2024"}],
                ],
            },
        }
        self.data = {
            ("1804", "117:119"): fixture_body("data_0000_1804"),
            ("2263", "124"): fixture_body("data_0000_2263"),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        assert p.pop("key") == FAKE_KEY
        path = request.url.path.removeprefix("/v1/api/")
        self.calls.append({"path": path, **p})
        if path == "domain":
            return httpx.Response(200, json=self.domains)
        assert path == "list"
        model = p["model"]
        if model == "var":
            assert p["domain"] == "0000"
            assert p["page"] == "1", "limit-vars 5 must not fetch page 2"
            return httpx.Response(200, json=self.var_page)
        if model == "th":
            return httpx.Response(200, json=self.th.get(p["var"], NOT_AVAILABLE))
        if model == "data":
            return httpx.Response(200, json=self.data.get((p["var"], p["th"]), NOT_AVAILABLE))
        raise AssertionError(f"unexpected request {p}")


@respx.mock
def test_seed_and_drain_fill_every_table(
    cli_env: None, db_engine: Engine, fixture_body: Callable[[str], Any]
) -> None:
    api = FakeApi(fixture_body)
    respx.route(url__startswith=BASE_URL).mock(side_effect=api)

    seeded = runner.invoke(app, ["seed", "dynamic", "--domain", "0000", "--limit-vars", "5"])
    assert seeded.exit_code == 0, seeded.output
    worked = runner.invoke(app, ["work", "--drain", "--concurrency", "2"])
    assert worked.exit_code == 0, worked.output

    first5 = api.var_ids[:5]
    th_vars = sorted(int(c["var"]) for c in api.calls if c.get("model") == "th")
    assert th_vars == sorted(first5)
    data_calls = sorted((c["var"], c["th"]) for c in api.calls if c.get("model") == "data")
    assert data_calls == [("1804", "117:119"), ("2263", "124")]

    assert _count(db_engine, domain) == 549
    assert _count(db_engine, variable) == 5
    assert _count(db_engine, period) == 4
    for t in (dim_vervar, dim_turvar, dim_turth, observation):
        assert _count(db_engine, t) > 0, t.name
    # 1 domain + 1 var page + 5 th + 2 data responses
    assert _count(db_engine, raw_response) == 9

    with db_engine.connect() as conn:
        done = select(task.c.kind, func.count()).where(task.c.status == "done")
        by_kind: dict[str, int] = dict(conn.execute(done.group_by(task.c.kind)).all())
        not_done = conn.execute(select(func.count()).where(task.c.status != "done")).scalar_one()
        obs_vars = set(conn.execute(select(observation.c.var_id).distinct()).scalars())
        leaked = conn.execute(
            text("SELECT count(*) FROM raw_response WHERE params::text LIKE :k"),
            {"k": f"%{FAKE_KEY}%"},
        ).scalar_one()
    assert by_kind == {"domains": 1, "var_list": 1, "th_list": 5, "data": 2}
    assert not_done == 0
    assert obs_vars == {1804, 2263}
    assert leaked == 0

    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0, status.output
    assert "th_list" in status.output
    assert "done" in status.output


def test_status_counts_by_kind_and_status(cli_env: None, db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task (kind, params, params_hash, status, last_error) VALUES"
                " ('data', '{}', 'a', 'done', NULL),"
                " ('data', '{\"x\": 1}', 'b', 'dead', 'BpsApiError: boom'),"
                " ('th_list', '{}', 'c', 'pending', NULL)"
            )
        )
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    lines = [line.split() for line in result.output.splitlines()]
    assert ["data", "done", "1"] in lines
    assert ["data", "dead", "1"] in lines
    assert ["th_list", "pending", "1"] in lines
