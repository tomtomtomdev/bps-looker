"""Task handlers. Importing this package registers every handler in ``worker.HANDLERS``."""

from bps_fetcher.handlers import domains, periods, variables

__all__ = ["domains", "periods", "variables"]
