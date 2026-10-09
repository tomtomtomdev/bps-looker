from pathlib import Path

import pytest
from pydantic import ValidationError

from bps_fetcher.settings import MissingApiKeyError, Settings, get_settings

FAKE_KEY = "abc123fakekey"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the real .env and any exported BPS_* vars out of these tests."""
    for name in ("BPS_API_KEY", "DATABASE_URL", "BPS_CONCURRENCY", "BPS_RPS", "BPS_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def test_loads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)
    assert settings.api_key.get_secret_value() == FAKE_KEY


def test_loads_key_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text(f"BPS_API_KEY={FAKE_KEY}\n")
    settings = Settings(_env_file=env_file)
    assert settings.api_key.get_secret_value() == FAKE_KEY


def test_loads_dotenv_in_cwd_by_default(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"BPS_API_KEY={FAKE_KEY}\n")
    assert get_settings().api_key.get_secret_value() == FAKE_KEY


def test_missing_key_raises_clear_error() -> None:
    with pytest.raises(ValidationError, match="BPS_API_KEY"):
        Settings(_env_file=None)


def test_empty_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", "  ")
    with pytest.raises(ValidationError, match="BPS_API_KEY"):
        Settings(_env_file=None)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.concurrency == 4
    assert settings.rps > 0
    assert "Mozilla/5.0" in settings.user_agent


def test_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("BPS_CONCURRENCY", "8")
    monkeypatch.setenv("BPS_RPS", "2.5")
    monkeypatch.setenv("BPS_USER_AGENT", "ua-test")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/x"
    assert settings.concurrency == 8
    assert settings.rps == 2.5
    assert settings.user_agent == "ua-test"


def test_key_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_get_settings_missing_key_has_actionable_message() -> None:
    with pytest.raises(MissingApiKeyError, match=r"BPS_API_KEY.*\.env"):
        get_settings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BPS_API_KEY", FAKE_KEY)
    assert get_settings() is get_settings()
