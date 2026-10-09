"""Keep the BPS API key out of URLs, strings and log records."""

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "***"

# ``key=<value>`` as a query parameter (preceded by ``?``/``&``, value up to ``&``/``#``/space).
_QUERY_KEY = re.compile(r"(?P<prefix>[?&]key=)[^&#\s'\"]+", re.IGNORECASE)
# ``/key/<value>`` as a path segment (BPS path-style URLs: ``.../key/<KEY>/``).
_PATH_KEY = re.compile(r"(?P<prefix>/key/)[^/?#\s'\"]+", re.IGNORECASE)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask API keys in ``text``: ``key=`` query params, ``/key/<x>/`` segments, known secrets."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _QUERY_KEY.sub(rf"\g<prefix>{REDACTED}", text)
    return _PATH_KEY.sub(rf"\g<prefix>{REDACTED}", text)


class RedactingFilter(logging.Filter):
    """Rewrite log records so neither the message, args nor traceback contain the key."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets: set[str] = {s for s in secrets if s}

    def add_secrets(self, secrets: Iterable[str]) -> None:
        self.secrets.update(s for s in secrets if s)

    def _redact(self, text: str) -> str:
        return redact(text, self.secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = self._redact(message)
        if clean != message:
            # Collapse to the formatted, redacted message so no arg can leak the key.
            record.msg = clean
            record.args = None
        elif isinstance(record.msg, str):
            # Message is clean once formatted; still scrub args/msg kept on the record.
            record.msg = self._redact(record.msg)
            record.args = self._redact_args(record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True

    def _redact_args(self, args: Any) -> Any:
        if isinstance(args, Mapping):
            return {k: self._redact(v) if isinstance(v, str) else v for k, v in args.items()}
        if isinstance(args, tuple):
            return tuple(self._redact(a) if isinstance(a, str) else a for a in args)
        return args


_installed: RedactingFilter | None = None


def install_redaction(secrets: Iterable[str] = ()) -> RedactingFilter:
    """Attach one shared :class:`RedactingFilter` to every handler on the root logger.

    Handler-level filters see records propagated from all loggers (logger-level filters on
    the root would not). Call again after adding handlers; repeated calls reuse the filter.
    """
    global _installed
    if _installed is None:
        _installed = RedactingFilter(secrets)
    else:
        _installed.add_secrets(secrets)
    for handler in logging.getLogger().handlers:
        if _installed not in handler.filters:
            handler.addFilter(_installed)
    return _installed
