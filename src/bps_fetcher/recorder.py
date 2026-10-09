"""Record live BPS responses as JSON test fixtures, with the API key scrubbed everywhere."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from bps_fetcher.client import BASE_URL
from bps_fetcher.redact import redact

DEFAULT_OUT_DIR = Path("tests/fixtures")
# An invalid key for the bad-key sample: the API answers a JSON auth error.
BAD_KEY = "deadbeefdeadbeefdeadbeefdeadbeef"
# A key the perimeter WAF itself rejects (403 HTML block page), for a real WAF sample.
WAF_KEY = "0000000000000000000000000000000x"


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    path: str
    params: Mapping[str, Any] = field(default_factory=dict)
    # Send this (fake) key instead of the real one, for error samples.
    fake_key: str | None = None


FIXTURES: tuple[FixtureSpec, ...] = (
    FixtureSpec("domain_all", "domain", {"type": "all"}),
    FixtureSpec("var_0000_p1", "list", {"model": "var", "domain": "0000", "page": 1}),
    FixtureSpec("th_0000_1804", "list", {"model": "th", "domain": "0000", "var": 1804}),
    FixtureSpec(
        "data_0000_1804", "list", {"model": "data", "domain": "0000", "var": 1804, "th": "117:119"}
    ),
    FixtureSpec(
        "data_0000_2263", "list", {"model": "data", "domain": "0000", "var": 2263, "th": 124}
    ),
    # Seen in the S13 national crawl: an empty window answers "list-not-available" with data "",
    # and a too-large window (514 regions x 41 categories x 3 years) answers JSON ``null``.
    FixtureSpec(
        "data_list_not_available",
        "list",
        {"model": "data", "domain": "0000", "var": 698, "th": "86:88"},
    ),
    FixtureSpec(
        "data_null_too_large",
        "list",
        {"model": "data", "domain": "0000", "var": 2096, "th": "118:120"},
    ),
    FixtureSpec("indicators_0000_p1", "list", {"model": "indicators", "domain": "0000", "page": 1}),
    FixtureSpec("indicators_0000_p2", "list", {"model": "indicators", "domain": "0000", "page": 2}),
    FixtureSpec(
        "trade_exp_annual_03_2024",
        "dataexim/",
        {"sumber": 1, "periode": 2, "kodehs": "03", "jenishs": 1, "tahun": 2024},
    ),
    FixtureSpec(
        "trade_exp_monthly_03_2024",
        "dataexim/",
        {"sumber": 1, "periode": 1, "kodehs": "03", "jenishs": 1, "tahun": 2024},
    ),
    FixtureSpec("error_bad_key", "list", {"model": "subcat", "domain": "0000"}, fake_key=BAD_KEY),
    FixtureSpec("error_waf_block", "list", {"model": "subcat", "domain": "0000"}, fake_key=WAF_KEY),
    FixtureSpec("error_data_missing_th", "list", {"model": "data", "domain": "0000", "var": 1804}),
    FixtureSpec(
        "error_data_too_many_th",
        "list",
        {"model": "data", "domain": "0000", "var": 1804, "th": "113:119"},
    ),
)


def scrub(value: Any, secrets: Iterable[str]) -> Any:
    """Return ``value`` with every string redacted (recursively through dicts and lists)."""
    secrets = list(secrets)
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {scrub(k, secrets): scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    return value


async def record(
    http: httpx.AsyncClient,
    spec: FixtureSpec,
    *,
    api_key: str,
    user_agent: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    base_url: str = BASE_URL,
    secrets: Iterable[str] = (),
) -> Path:
    """GET ``spec`` once (no retries, errors kept as-is) and write ``<out_dir>/<name>.json``.

    The key is removed from the stored URL, params and body; ``secrets`` are scrubbed too.
    """
    all_secrets = [api_key, *secrets]
    params = {k: str(v) for k, v in spec.params.items()}
    response = await http.get(
        base_url + spec.path.lstrip("/"),
        params={**params, "key": api_key},
        headers={"User-Agent": user_agent},
    )
    try:
        body: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = response.text
    fixture = {
        "name": spec.name,
        "request": {
            "method": "GET",
            "url": str(response.request.url),
            "path": spec.path,
            "params": params,
        },
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "body": body,
    }
    text = json.dumps(scrub(fixture, all_secrets), ensure_ascii=False, indent=1) + "\n"
    for secret in all_secrets:  # belt and braces: nothing may survive serialisation
        if secret and secret in text:
            raise RuntimeError(f"refusing to write {spec.name}: key still present")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}.json"
    path.write_text(text, encoding="utf-8")
    return path
