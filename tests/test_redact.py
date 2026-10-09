import logging
from collections.abc import Iterator

import pytest

from bps_fetcher.redact import REDACTED, RedactingFilter, install_redaction, redact

KEY = "abc123fakekey"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://webapi.bps.go.id/v1/api/list?model=var&domain=0000&key={KEY}",
            f"https://webapi.bps.go.id/v1/api/list?model=var&domain=0000&key={REDACTED}",
        ),
        (
            f"https://webapi.bps.go.id/v1/api/list?key={KEY}&model=var",
            f"https://webapi.bps.go.id/v1/api/list?key={REDACTED}&model=var",
        ),
        (
            f"https://webapi.bps.go.id/v1/api/list/model/th/domain/0000/var/1804/key/{KEY}/",
            f"https://webapi.bps.go.id/v1/api/list/model/th/domain/0000/var/1804/key/{REDACTED}/",
        ),
        (
            f"https://webapi.bps.go.id/v1/api/list/model/th/key/{KEY}",
            f"https://webapi.bps.go.id/v1/api/list/model/th/key/{REDACTED}",
        ),
        (
            f"https://webapi.bps.go.id/v1/api/dataexim/?sumber=1&tahun=2024&key={KEY}#frag",
            f"https://webapi.bps.go.id/v1/api/dataexim/?sumber=1&tahun=2024&key={REDACTED}#frag",
        ),
        (
            "https://webapi.bps.go.id/v1/api/list?model=var&monkey=1",
            "https://webapi.bps.go.id/v1/api/list?model=var&monkey=1",
        ),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact(url) == expected


def test_redact_known_secret_anywhere() -> None:
    text = f"error body mentions {KEY} twice: {KEY}"
    assert redact(text, secrets=[KEY]) == f"error body mentions {REDACTED} twice: {REDACTED}"


def test_redact_ignores_empty_secret() -> None:
    assert redact("nothing here", secrets=[""]) == "nothing here"


@pytest.fixture
def redacting_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("bps_fetcher.test_redact")
    flt = RedactingFilter(secrets=[KEY])
    logger.addFilter(flt)
    yield logger
    logger.removeFilter(flt)


def test_log_record_never_contains_key(
    redacting_logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=redacting_logger.name):
        redacting_logger.info("GET https://x/v1/api/list?key=%s", KEY)
        redacting_logger.info("GET https://x/v1/api/list/model/th/key/%s/", KEY)
        redacting_logger.warning(f"raw message with {KEY}")
        redacting_logger.info("dict args %(url)s", {"url": f"https://x/?key={KEY}"})
        redacting_logger.info("non-string arg %s %d", KEY, 3)
    assert len(caplog.records) == 5
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in str(record.msg)
        assert KEY not in repr(record.args)
    assert caplog.records[4].getMessage() == f"non-string arg {REDACTED} 3"
    assert KEY not in caplog.text


def test_install_redaction_on_root_handlers(caplog: pytest.LogCaptureFixture) -> None:
    root = logging.getLogger()
    installed = install_redaction(secrets=[KEY])
    try:
        assert any(isinstance(f, RedactingFilter) for f in caplog.handler.filters)
        with caplog.at_level(logging.INFO):
            logging.getLogger("some.thirdparty").info("url=https://x/?key=%s", KEY)
        assert KEY not in caplog.text
        assert REDACTED in caplog.text
    finally:
        for handler in root.handlers:
            handler.removeFilter(installed)


def test_install_redaction_is_idempotent() -> None:
    root = logging.getLogger()
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        first = install_redaction(secrets=[KEY])
        second = install_redaction(secrets=[KEY])
        assert first is second
        assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1
    finally:
        root.removeHandler(handler)


def test_exception_text_is_redacted(
    redacting_logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=redacting_logger.name):
        try:
            raise RuntimeError(f"request failed: https://x/v1/api/list?key={KEY}")
        except RuntimeError:
            redacting_logger.exception("boom")
    assert KEY not in caplog.text
    assert REDACTED in caplog.text
