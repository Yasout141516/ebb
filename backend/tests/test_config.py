from zoneinfo import ZoneInfo

import pytest

from ebb import config
from ebb.config import ConfigError, ModelSpec, Settings, parse_model, resolve_timezone


def test_resolve_timezone_accepts_a_name():
    assert resolve_timezone("Asia/Dhaka") == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_strips_spaces():
    assert resolve_timezone("  Asia/Dhaka ") == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_rejects_unknown_names():
    with pytest.raises(ConfigError, match="EBB_TIMEZONE 'Mars/Olympus' is not a known time zone"):
        resolve_timezone("Mars/Olympus")


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_resolve_timezone_unset_uses_the_computer(monkeypatch, unset):
    monkeypatch.setattr(config.tzlocal, "get_localzone_name", lambda: "Asia/Dhaka")
    assert resolve_timezone(unset) == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_falls_back_to_utc(monkeypatch):
    def boom():
        raise RuntimeError("no zone")

    monkeypatch.setattr(config.tzlocal, "get_localzone_name", boom)
    assert resolve_timezone(None) == ZoneInfo("UTC")


def test_resolve_timezone_falls_back_to_utc_when_the_computer_gives_none(monkeypatch):
    monkeypatch.setattr(config.tzlocal, "get_localzone_name", lambda: None)
    assert resolve_timezone(None) == ZoneInfo("UTC")


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_parse_model_splits_provider_and_model():
    assert parse_model("openrouter:qwen/x", "EBB_TEACH_MODEL") == ModelSpec("openrouter", "qwen/x")


def test_parse_model_rejects_unknown_provider():
    with pytest.raises(ConfigError, match="EBB_QUIZ_MODEL must look like provider:model"):
        parse_model("openai:gpt-4", "EBB_QUIZ_MODEL")
    with pytest.raises(ConfigError, match="EBB_QUIZ_MODEL"):
        parse_model("groq:", "EBB_QUIZ_MODEL")


def test_defaults_match_the_documented_models():
    models = settings().role_models()
    groq = ModelSpec("groq", "openai/gpt-oss-120b")
    assert models["teach"] == (groq, None) and models["quiz"] == (groq, None)
    assert models["eval_writer"] == (ModelSpec("mistral", "mistral-medium-latest"), None)
    assert settings().ebb_show_costs is True


def test_legacy_primary_model_is_the_default():
    models = settings(
        ebb_primary_model="a/b", ebb_fallback_model="m-small", mistral_api_key="m"
    ).role_models()
    mistral = ModelSpec("mistral", "m-small")
    assert models["teach"] == (ModelSpec("groq", "a/b"), mistral)
    assert models["quiz"] == (ModelSpec("groq", "a/b"), mistral)


def test_role_settings_beat_the_legacy_ones():
    models = settings(
        ebb_primary_model="a/b",
        ebb_teach_model="openrouter:q/x",
        ebb_teach_backup="groq:c/d",
        mistral_api_key="m",
    ).role_models()
    assert models["teach"] == (ModelSpec("openrouter", "q/x"), ModelSpec("groq", "c/d"))
    assert models["quiz"][0] == ModelSpec("groq", "a/b")


def test_a_bad_role_setting_names_itself():
    with pytest.raises(ConfigError, match="EBB_TEACH_BACKUP"):
        settings(ebb_teach_backup="nope").role_models()
