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
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    tavily_api_key: str | None = None
    ebb_primary_model: str = "openai/gpt-oss-120b"
    ebb_fallback_model: str = "mistral-medium-latest"
    ebb_dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    ebb_timezone: str | None = None
    ebb_daily_ai_calls: int = 300
    ebb_web_origin: str = "http://localhost:3000"


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
