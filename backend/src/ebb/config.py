"""Settings from environment variables (and a .env file when run locally)."""

from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A missing setting or unreachable service; the CLI prints it as one line."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://ebb:ebb@localhost:5432/ebb"
    google_api_key: str | None = None
    groq_api_key: str | None = None
    tavily_api_key: str | None = None
    ebb_primary_model: str = "gemini-3.8-flash"
    ebb_fallback_model: str = "qwen/qwen3.8-27b"
    ebb_dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    ebb_timezone: str | None = None


def resolve_timezone(name: str | None) -> ZoneInfo:
    """The zone dates are shown in: the named one, else the computer's, else UTC."""
    name = (name or "").strip()
    if not name:
        try:
            return ZoneInfo(tzlocal.get_localzone_name())
        except ZoneInfoNotFoundError, ValueError, OSError, RuntimeError, TypeError:
            return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"EBB_TIMEZONE '{name}' is not a known time zone") from error
