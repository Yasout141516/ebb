"""Settings from environment variables (and a .env file when run locally)."""

from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A missing setting or unreachable service; the CLI prints it as one line."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://ebb:ebb@localhost:5432/ebb"
    google_api_key: str | None = None
    groq_api_key: str | None = None
    ebb_primary_model: str = "gemini-2.5-flash"
    ebb_fallback_model: str = "llama-3.3-70b-versatile"
    ebb_dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
