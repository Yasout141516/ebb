import pytest
from langchain_groq import ChatGroq
from langchain_mistralai import ChatMistralAI
from langchain_openai import ChatOpenAI

from ebb.config import ConfigError, ModelSpec, Settings
from ebb.llm.providers import chat_models
from ebb.llm.tutor import LangChainTutor


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_openrouter_builds_an_openai_client_pointed_at_openrouter():
    provider = chat_models(
        ModelSpec("openrouter", "qwen/x"), settings(openrouter_api_key="o"), "EBB_TEACH_MODEL"
    )
    assert isinstance(provider.creative, ChatOpenAI)
    assert provider.creative.openai_api_base == "https://openrouter.ai/api/v1"
    assert (provider.provider, provider.name, provider.service) == (
        "openrouter",
        "qwen/x",
        "OpenRouter",
    )
    assert (provider.precise.temperature, provider.creative.temperature) == (0.2, 0.7)


def test_groq_and_mistral_build_their_own_clients():
    s = settings(groq_api_key="g", mistral_api_key="m")
    assert isinstance(chat_models(ModelSpec("groq", "a"), s, "S").precise, ChatGroq)
    assert isinstance(chat_models(ModelSpec("mistral", "a"), s, "S").precise, ChatMistralAI)


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("openrouter", "OPENROUTER_API_KEY is not set — EBB_EVAL_WRITER_MODEL uses OpenRouter"),
        ("mistral", "MISTRAL_API_KEY is not set — EBB_EVAL_WRITER_MODEL uses Mistral"),
        (
            "groq",
            "GROQ_API_KEY is not set — EBB_EVAL_WRITER_MODEL uses Groq "
            "(get one free at console.groq.com)",
        ),
    ],
)
def test_missing_key_names_the_setting(provider, message):
    with pytest.raises(ConfigError) as caught:
        chat_models(ModelSpec(provider, "m"), settings(), "EBB_EVAL_WRITER_MODEL")
    assert str(caught.value) == message


def test_missing_openrouter_key_names_the_setting():
    with pytest.raises(ConfigError) as caught:
        LangChainTutor.from_settings(
            settings(groq_api_key="g", ebb_teach_model="openrouter:qwen/x")
        )
    assert str(caught.value) == "OPENROUTER_API_KEY is not set — EBB_TEACH_MODEL uses OpenRouter"


def test_each_role_gets_its_own_chain():
    tutor = LangChainTutor.from_settings(
        settings(
            groq_api_key="g",
            openrouter_api_key="o",
            ebb_teach_model="openrouter:qwen/x",
            ebb_quiz_backup="openrouter:q/y",
        )
    )
    assert [p.name for p in tutor.chains["teach"]] == ["qwen/x"]
    assert [p.name for p in tutor.chains["quiz"]] == ["openai/gpt-oss-120b", "q/y"]


def test_eval_writer_equal_to_grader_is_refused():
    with pytest.raises(ConfigError) as caught:
        LangChainTutor.writer(
            settings(
                mistral_api_key="m",
                ebb_quiz_model="mistral:mistral-medium-latest",
            )
        )
    assert str(caught.value) == "EBB_EVAL_WRITER_MODEL must differ from EBB_QUIZ_MODEL (the grader)"


def test_the_older_fallback_setting_still_picks_the_eval_writer():
    assert settings(ebb_fallback_model="mistral-small-latest").role_models()["eval_writer"] == (
        ModelSpec("mistral", "mistral-small-latest"),
        None,
    )
    assert settings().role_models()["eval_writer"][0] == ModelSpec(
        "mistral", "mistral-medium-latest"
    )
    chosen = settings(ebb_fallback_model="x", ebb_eval_writer_model="openrouter:w/z")
    assert chosen.role_models()["eval_writer"][0] == ModelSpec("openrouter", "w/z")
