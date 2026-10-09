"""Live smoke tests against the real BPS WebAPI. Run with ``make live`` (needs BPS_API_KEY)."""

import pytest
from sqlalchemy import Engine, text

from bps_fetcher.client import BpsAuthError, BpsClient
from bps_fetcher.paginate import paginate
from bps_fetcher.recorder import BAD_KEY
from bps_fetcher.settings import get_settings

pytestmark = pytest.mark.live


async def test_live_subcat_and_domains() -> None:
    async with BpsClient.from_settings(get_settings()) as client:
        body = await client.get("list", model="subcat", domain="0000")
        assert body["status"] == "OK"
        provinces = [d async for d in paginate(client, "domain", type="prov")]
        assert len(provinces) >= 34
        assert all(len(d["domain_id"]) == 4 for d in provinces)


async def test_live_bad_key_is_auth_error() -> None:
    settings = get_settings()
    async with BpsClient(api_key=BAD_KEY, user_agent=settings.user_agent, rps=1) as client:
        with pytest.raises(BpsAuthError):
            await client.get("list", model="subcat", domain="0000")


def test_live_cli_national_three_vars(monkeypatch: pytest.MonkeyPatch, db_engine: Engine) -> None:
    """Real ``bps seed dynamic --domain 0000 --limit-vars 3`` + ``bps work --drain`` (bps_test)."""
    from typer.testing import CliRunner

    from bps_fetcher.cli import app

    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        seeded = runner.invoke(app, ["seed", "dynamic", "--domain", "0000", "--limit-vars", "3"])
        assert seeded.exit_code == 0, seeded.output
        worked = runner.invoke(app, ["work", "--drain"])
        assert worked.exit_code == 0, worked.output
    finally:
        get_settings.cache_clear()

    with db_engine.connect() as conn:
        by_status = text("SELECT status, count(*) FROM task GROUP BY status")
        counts: dict[str, int] = dict(conn.execute(by_status).all())
        n_vars = conn.execute(text("SELECT count(*) FROM variable")).scalar_one()
        n_obs = conn.execute(text("SELECT count(*) FROM observation")).scalar_one()
        n_th = conn.execute(text("SELECT count(*) FROM task WHERE kind = 'th_list'")).scalar_one()
    assert set(counts) == {"done"}, counts
    assert n_vars == 3
    assert n_th == 3
    assert n_obs > 0
