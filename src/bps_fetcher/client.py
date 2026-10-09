"""Async HTTP client for the BPS WebAPI: UA + key, rate limiting, retries, error mapping."""

import json
import logging
from types import TracebackType
from typing import Any, Self

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from bps_fetcher.redact import RedactingFilter, redact
from bps_fetcher.settings import Settings

BASE_URL = "https://webapi.bps.go.id/v1/api/"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_ATTEMPTS = 5

_AUTH_MARKERS = ("re-check your key", "not allowed")

log = logging.getLogger(__name__)

# httpx logs every request URL (including ``key=``) at INFO on its own logger; scrub it there
# so the key is gone before records propagate to any handler.
_HTTPX_FILTER = RedactingFilter()
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_HTTPX_FILTER)


class BpsError(Exception):
    """Base class for all BPS client errors."""


class BpsApiError(BpsError):
    """The API answered with ``"status": "Error"`` (or an unusable body). Not retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BpsAuthError(BpsApiError):
    """The API rejected the key. Not retried."""


class BpsTransientError(BpsError):
    """WAF block, 5xx, timeout or network error that persisted after all retries."""


class _Retryable(Exception):
    """Internal marker: this attempt failed in a way worth retrying."""


class BpsClient:
    """Thin async wrapper over ``httpx`` returning the decoded JSON object of each call."""

    def __init__(
        self,
        *,
        api_key: str,
        user_agent: str,
        rps: float,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        wait: wait_base | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be > 0")
        self._api_key = api_key
        _HTTPX_FILTER.add_secrets([api_key])
        self.user_agent = user_agent
        self.rps = rps
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.max_attempts = max_attempts
        self._wait = wait if wait is not None else wait_exponential_jitter(initial=1, max=30)
        # One token per 1/rps seconds: a strict cap with no initial burst.
        self._limiter = AsyncLimiter(1, 1 / rps)
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> Self:
        return cls(
            api_key=settings.api_key.get_secret_value(),
            user_agent=settings.user_agent,
            rps=settings.rps,
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"BpsClient(base_url={self.base_url!r}, rps={self.rps}, key=***)"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _redact(self, text: str) -> str:
        return redact(text, [self._api_key])

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET ``<base_url><path>`` with ``params`` + key; return the JSON object.

        Raises :class:`BpsAuthError`, :class:`BpsApiError` or :class:`BpsTransientError`.
        No exception message ever contains the API key.
        """
        url = self.base_url + path.lstrip("/")
        query = {k: str(v) for k, v in params.items()}
        query["key"] = self._api_key
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(_Retryable),
            before_sleep=self._log_retry,
            reraise=False,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._once(url, query)
        except RetryError as err:
            cause = err.last_attempt.exception()
            detail = self._redact(str(cause)) if cause else "unknown error"
            raise BpsTransientError(
                f"GET {path} failed after {self.max_attempts} attempts: {detail}"
            ) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        sleep = state.next_action.sleep if state.next_action else 0.0
        log.warning(
            "BPS request failed (attempt %d/%d), retrying in %.1fs: %s",
            state.attempt_number,
            self.max_attempts,
            sleep,
            self._redact(str(exc)),
        )

    async def _once(self, url: str, query: dict[str, str]) -> dict[str, Any]:
        async with self._limiter:
            try:
                response = await self._http.get(
                    url, params=query, headers={"User-Agent": self.user_agent}
                )
            except httpx.TransportError as exc:  # timeouts, connect/read errors
                raise _Retryable(self._redact(f"{type(exc).__name__}: {exc}")) from None

        if response.status_code >= 500:
            raise _Retryable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            # WAF block page (HTML) or a truncated body: worth another try.
            raise _Retryable(
                f"HTTP {response.status_code} non-JSON body "
                f"({response.headers.get('content-type', '?')})"
            ) from None
        if not isinstance(body, dict):
            raise BpsApiError(f"unexpected JSON {type(body).__name__} from {response.url.path}")
        if body.get("status") == "Error" or response.is_client_error:
            message = self._redact(str(body.get("message") or f"HTTP {response.status_code}"))
            if any(marker in message.lower() for marker in _AUTH_MARKERS):
                raise BpsAuthError(message)
            raise BpsApiError(message)
        return body
