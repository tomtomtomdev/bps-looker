"""Live smoke tests against the real BPS WebAPI. Run with ``make live`` (needs BPS_API_KEY)."""

import pytest

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
