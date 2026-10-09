"""Task handlers. Importing this package registers every handler in ``worker.HANDLERS``."""

from bps_fetcher.handlers import data, domains, indicators, periods, variables

__all__ = ["data", "domains", "indicators", "periods", "variables"]
