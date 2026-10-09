"""Task handlers. Importing this package registers every handler in ``worker.HANDLERS``."""

from bps_fetcher.handlers import domains, variables

__all__ = ["domains", "variables"]
