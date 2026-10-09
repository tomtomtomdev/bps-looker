"""Runtime configuration loaded from the environment and an optional ``.env`` file."""

from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) bps-looker"


class MissingApiKeyError(RuntimeError):
    """Raised when ``BPS_API_KEY`` is not configured."""


DEFAULT_DATABASE_URL = "postgresql+psycopg://bps:bps@localhost:5432/bps"


class DatabaseSettings(BaseSettings):
    """Just the database URL — usable without ``BPS_API_KEY`` (migrations, DB tools)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(default=DEFAULT_DATABASE_URL, validation_alias="DATABASE_URL")


class Settings(DatabaseSettings):
    api_key: SecretStr = Field(validation_alias="BPS_API_KEY")
    concurrency: int = Field(default=4, ge=1, validation_alias="BPS_CONCURRENCY")
    rps: float = Field(default=2.0, gt=0, validation_alias="BPS_RPS")
    user_agent: str = Field(
        default=DEFAULT_USER_AGENT,
        min_length=1,
        validation_alias="BPS_USER_AGENT",
    )

    @field_validator("api_key")
    @classmethod
    def _key_not_blank(cls, value: SecretStr) -> SecretStr:
        stripped = value.get_secret_value().strip()
        if not stripped:
            raise ValueError("BPS_API_KEY is empty")
        return SecretStr(stripped)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once; raise :class:`MissingApiKeyError` with a hint if the key is absent."""
    try:
        return Settings()
    except ValidationError as exc:
        if any("BPS_API_KEY" in map(str, err["loc"]) for err in exc.errors()):
            raise MissingApiKeyError(
                "BPS_API_KEY is not set. Export it or add it to .env (see .env.example)."
            ) from None
        raise
