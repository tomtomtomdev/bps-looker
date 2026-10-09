"""Probe the BPS foreign-trade endpoint (calls the real API; needs BPS_API_KEY).

Answers the S15 discovery questions:
1. What is the earliest year with data (2014 and earlier)?
2. Which 2-digit chapters are unused (77, 98, 99)?
3. Does ``jenishs=2`` (full HS codes) work with any code format?

Usage: uv run python scripts/trade_discovery.py [--env-file PATH] [LABEL_SUBSTRING ...]
(substrings pick a subset of the probes; default: all)
At most ~20 calls, >= 1 s apart. The key is read from .env via Settings and never printed.
"""

import asyncio
import sys
from collections import Counter
from typing import Any

from bps_fetcher.client import BpsClient, BpsError
from bps_fetcher.settings import Settings, get_settings
from bps_fetcher.trade import EXPORT, IMPORT, MONTHLY, YEARLY, parse_bracket

# (label, params) — every probe is one call.
PROBES: list[tuple[str, dict[str, Any]]] = [
    # 1. earliest year (annual exports, chapter 03; imports for the first missing year)
    *[
        (f"year {y} export annual 03", {"sumber": EXPORT, "periode": YEARLY, "tahun": y})
        for y in (2016, 2015, 2014, 2013, 2012, 2010)
    ],
    ("year 2014 import annual 03", {"sumber": IMPORT, "periode": YEARLY, "tahun": 2014}),
    ("year 2014 export monthly 03", {"sumber": EXPORT, "periode": MONTHLY, "tahun": 2014}),
    # 2. reserved / national-use chapters
    *[
        (f"chapter {c} export annual 2024", {"sumber": EXPORT, "periode": YEARLY, "kodehs": c})
        for c in ("77", "98", "99")
    ],
    ("chapter 99 import annual 2024", {"sumber": IMPORT, "periode": YEARLY, "kodehs": "99"}),
    # 3. jenishs=2 code formats
    *[
        (
            f"jenishs=2 kodehs={code}",
            {"sumber": EXPORT, "periode": YEARLY, "jenishs": 2, "kodehs": code},
        )
        for code in ("03", "0301", "030111", "03011110", "03011110;03011190", "0301.11.10")
    ],
]
DEFAULTS: dict[str, Any] = {"kodehs": "03", "jenishs": 1, "tahun": 2024}


def summarize(body: dict[str, Any]) -> str:
    rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        return f"status={body.get('status')} availability={body.get('data-availability')} rows=0"
    codes = Counter(parse_bracket(r["kodehs"])[0] for r in rows if "kodehs" in r)
    return (
        f"status={body.get('status')} availability={body.get('data-availability')} "
        f"rows={len(rows)} codes={dict(codes.most_common(5))} sample={rows[0]}"
    )


async def main(argv: list[str]) -> int:
    if argv[:1] == ["--env-file"]:
        if len(argv) < 2:
            print(__doc__, file=sys.stderr)
            return 2
        settings = Settings(_env_file=argv[1])  # type: ignore[call-arg]
        argv = argv[2:]
    else:
        settings = get_settings()
    probes = [p for p in PROBES if not argv or any(sub in p[0] for sub in argv)]
    async with BpsClient.from_settings(settings, max_attempts=2) as client:
        for i, (label, params) in enumerate(probes):
            if i:
                await asyncio.sleep(1.0)
            query = {**DEFAULTS, **params}
            try:
                result = summarize(await client.get("dataexim/", **query))
            except BpsError as exc:
                result = f"{type(exc).__name__}: {exc}"
            print(f"{label}: {result}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
