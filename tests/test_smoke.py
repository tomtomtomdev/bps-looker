import bps_fetcher


def test_version_is_non_empty_string() -> None:
    assert isinstance(bps_fetcher.__version__, str)
    assert bps_fetcher.__version__
