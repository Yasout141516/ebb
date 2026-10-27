"""Chat models for each provider, built from the model settings."""

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from ebb.config import ConfigError, ModelSpec, Role, Settings

__all__ = ["REQUEST_TIMEOUT_SECONDS", "Provider", "Role", "chat_models"]

REQUEST_TIMEOUT_SECONDS = 60

OPENROUTER_URL = "https://openrouter.ai/api/v1"

SERVICES = {"groq": "Groq", "openrouter": "OpenRouter", "mistral": "Mistral"}
KEY_NAMES = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
# Settings whose Groq key was always required, with the longer hint users already know.
_GROQ_HINT_SETTINGS = ("EBB_TEACH_MODEL", "EBB_QUIZ_MODEL")


@dataclass(frozen=True)
class Provider:
    name: str  # the model name
    precise: BaseChatModel  # low temperature: grading
    creative: BaseChatModel  # higher temperature: generating and teaching
    service: str = "Groq"
    key_name: str = "GROQ_API_KEY"
    provider: str = "groq"


def _api_key(spec: ModelSpec, settings: Settings, setting: str) -> str:
    key = getattr(settings, KEY_NAMES[spec.provider].lower())
    if key:
        return key
    if spec.provider == "groq" and setting in _GROQ_HINT_SETTINGS:
        raise ConfigError("GROQ_API_KEY is not set (get one free at console.groq.com)")
    raise ConfigError(
        f"{KEY_NAMES[spec.provider]} is not set — {setting} uses {SERVICES[spec.provider]}"
    )


def chat_models(spec: ModelSpec, settings: Settings, setting: str) -> Provider:
    """The model `spec` names, as a precise (0.2) and a creative (0.7) streaming client."""
    api_key = _api_key(spec, settings, setting)

    def build(temperature: float) -> BaseChatModel:
        match spec.provider:
            case "groq":
                from langchain_groq import ChatGroq

                return ChatGroq(
                    model_name=spec.model,
                    groq_api_key=api_key,
                    temperature=temperature,
                    max_retries=1,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    streaming=True,
                )
            case "mistral":
                from langchain_mistralai import ChatMistralAI

                return ChatMistralAI(
                    model=spec.model,
                    api_key=api_key,
                    temperature=temperature,
                    max_retries=1,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    streaming=True,
                )
            case _:
                from langchain_openai import ChatOpenAI

                return ChatOpenAI(
                    model=spec.model,
                    api_key=api_key,
                    base_url=OPENROUTER_URL,
                    temperature=temperature,
                    max_retries=1,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    streaming=True,
                    stream_usage=True,
                )

    return Provider(
        spec.model,
        build(0.2),
        build(0.7),
        service=SERVICES[spec.provider],
        key_name=KEY_NAMES[spec.provider],
        provider=spec.provider,
    )
