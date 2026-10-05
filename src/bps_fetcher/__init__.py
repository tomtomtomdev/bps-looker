"""Crawl the BPS (Statistics Indonesia) WebAPI into Postgres."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("bps-fetcher")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
