"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_QUERY_STATEMENT_TIMEOUT_MS = 5_000
DEFAULT_QUERY_MAX_ROWS = 500
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class Settings(BaseSettings):
    """Configuration required by the FastAPI process."""

    app_title: str = "Banking AI SQL Analyst API"
    app_version: str = "0.1.0"
    groq_api_key: SecretStr | None = None
    groq_model: str = DEFAULT_GROQ_MODEL
    groq_reasoning_effort: str = "medium"
    banking_reader_user: str = "banking_reader"
    banking_reader_database_url: str | None = None
    query_statement_timeout_ms: int = Field(
        default=DEFAULT_QUERY_STATEMENT_TIMEOUT_MS, gt=0
    )
    query_max_rows: int = Field(default=DEFAULT_QUERY_MAX_ROWS, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Load and cache settings from the process environment and local .env file."""
    return Settings()
