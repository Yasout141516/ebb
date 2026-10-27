"""Settings from environment variables (and a .env file when run locally)."""

from dataclasses import dataclass
from typing import Literal, get_args
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A missing setting or unreachable service; the CLI prints it as one line."""


Role = Literal["teach", "quiz", "eval_writer"]
ProviderName = Literal["groq", "openrouter", "mistral"]


@dataclass(frozen=True)
class ModelSpec:
    provider: ProviderName
    model: str


def parse_model(value: str, setting: str) -> ModelSpec:
    """`provider:model` (e.g. `groq:openai/gpt-oss-120b`); the setting is named in the error."""
    provider, _, model = value.strip().partition(":")
    if provider not in get_args(ProviderName) or not model.strip():
        raise ConfigError(
            f"{setting} must look like provider:model, with provider one of "
            f"{', '.join(get_args(ProviderName))} (got {value!r})"
        )
    return ModelSpec(provider, model.strip())  # type: ignore[arg-type]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://ebb:ebb@localhost:5432/ebb"
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    openrouter_api_key: str | None = None
    tavily_api_key: str | None = None
    # Older settings: a bare Groq / Mistral model name, the defaults of the role models below.
    ebb_primary_model: str | None = None
    ebb_fallback_model: str | None = None
    ebb_teach_model: str | None = None
    ebb_quiz_model: str | None = None
    ebb_eval_writer_model: str = "mistral:mistral-medium-latest"
    ebb_teach_backup: str | None = None
    ebb_quiz_backup: str | None = None
    ebb_show_costs: bool = True
    ebb_dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    ebb_timezone: str | None = None
    ebb_daily_ai_calls: int = 300
    # The website's address, or several separated by commas.
    ebb_web_origin: str = "http://localhost:3000,http://127.0.0.1:3000"

    def role_models(self) -> dict[Role, tuple[ModelSpec, ModelSpec | None]]:
        """Each role's main model and its backup (None: no backup)."""
        main = f"groq:{self.ebb_primary_model or 'openai/gpt-oss-120b'}"
        legacy_backup = ModelSpec("mistral", self.ebb_fallback_model or "mistral-medium-latest")

        def pair(
            model: str | None, backup: str | None, model_setting: str, backup_setting: str
        ) -> tuple[ModelSpec, ModelSpec | None]:
            chosen = parse_model(model or main, model_setting)
            if backup:
                return chosen, parse_model(backup, backup_setting)
            # Today's behaviour: a Mistral key is what turns the backup on.
            return chosen, legacy_backup if self.mistral_api_key else None

        writer = parse_model(self.ebb_eval_writer_model, "EBB_EVAL_WRITER_MODEL")
        return {
            "teach": pair(
                self.ebb_teach_model, self.ebb_teach_backup, "EBB_TEACH_MODEL", "EBB_TEACH_BACKUP"
            ),
            "quiz": pair(
                self.ebb_quiz_model, self.ebb_quiz_backup, "EBB_QUIZ_MODEL", "EBB_QUIZ_BACKUP"
            ),
            "eval_writer": (writer, None),
        }


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
