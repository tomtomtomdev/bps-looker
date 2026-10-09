"""Record live BPS responses into tests/fixtures/ (calls the real API; needs BPS_API_KEY).

Usage: uv run python scripts/record_fixture.py [NAME ...]   # default: every fixture
The key is read from .env via Settings and never printed or stored.
"""

import asyncio
import sys
import time

import httpx

from bps_fetcher.client import DEFAULT_TIMEOUT
from bps_fetcher.recorder import FIXTURES, record
from bps_fetcher.settings import get_settings

MIN_INTERVAL = 1.0  # seconds between calls; be polite regardless of BPS_RPS


async def main(names: list[str]) -> int:
    unknown = set(names) - {s.name for s in FIXTURES}
    if unknown:
        print(f"unknown fixture(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    specs = [s for s in FIXTURES if not names or s.name in names]
    settings = get_settings()
    real_key = settings.api_key.get_secret_value()
    interval = max(MIN_INTERVAL, 1 / settings.rps)
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
        for i, spec in enumerate(specs):
            if i:
                await asyncio.sleep(interval)
            started = time.monotonic()
            path = await record(
                http,
                spec,
                api_key=spec.fake_key or real_key,
                user_agent=settings.user_agent,
                secrets=[real_key],
            )
            took = time.monotonic() - started
            print(f"{spec.name}: {path} ({path.stat().st_size:,} bytes, {took:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
