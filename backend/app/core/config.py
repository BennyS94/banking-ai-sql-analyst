"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration required by the FastAPI process."""

    app_title: str = "Banking AI SQL Analyst API"
    app_version: str = "0.1.0"
    banking_reader_user: str = "banking_reader"
    banking_reader_database_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Load and cache settings from the process environment and local .env file."""
    return Settings()
