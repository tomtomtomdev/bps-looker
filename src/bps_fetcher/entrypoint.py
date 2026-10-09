"""Container entrypoint: migrate the database to head, then ``exec`` the given command.

    python -m bps_fetcher.entrypoint bps work --drain      # Dockerfile ENTRYPOINT + CMD

The database may still be starting when the container does, so an unreachable DB is retried
(``attempts`` x ``delay``) before giving up with exit 3 — the command never runs on an unmigrated
DB. ``BPS_MIGRATE_ON_START=0`` skips the migration (e.g. one-off ``--help`` without a DB).
``exec`` replaces this process, so the command gets PID/signals directly and its exit code is the
container's. No command → ``bps --help``.
"""

import os
import sys
import time
from collections.abc import Sequence

from sqlalchemy.exc import OperationalError

from bps_fetcher.db.migrate import upgrade
from bps_fetcher.redact import redact

DEFAULT_COMMAND = ("bps", "--help")
MIGRATE_FAILED_EXIT = 3
DEFAULT_ATTEMPTS = 30
DEFAULT_DELAY = 2.0
_FALSE = {"0", "false", "no", "off"}


def _log(message: str) -> None:
    print(f"entrypoint: {redact(message)}", file=sys.stderr, flush=True)


def migrate_on_start(attempts: int = DEFAULT_ATTEMPTS, delay: float = DEFAULT_DELAY) -> None:
    """Upgrade to head, retrying while the DB is unreachable; exit 3 when it never comes up."""
    for attempt in range(1, attempts + 1):
        try:
            upgrade(None, "head")
            _log("database migrated to head")
            return
        except OperationalError as exc:
            reason = (str(exc.orig if exc.orig is not None else exc).splitlines() or [""])[0]
            if attempt == attempts:
                _log(f"migration failed after {attempts} attempt(s): {reason}")
                raise SystemExit(MIGRATE_FAILED_EXIT) from None
            _log(f"database not reachable (attempt {attempt}/{attempts}): {reason}")
            time.sleep(delay)


def main(
    argv: Sequence[str] | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay: float = DEFAULT_DELAY,
) -> None:
    command = list(sys.argv[1:] if argv is None else argv) or list(DEFAULT_COMMAND)
    if os.environ.get("BPS_MIGRATE_ON_START", "1").strip().lower() not in _FALSE:
        migrate_on_start(attempts, delay)
    os.execvp(command[0], command)


if __name__ == "__main__":  # pragma: no cover
    main()
