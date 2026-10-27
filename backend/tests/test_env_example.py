import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_no_inline_comments_on_assignment_lines():
    # Docker Compose keeps a trailing "# ..." as part of the value.
    bad = [
        line
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*[A-Z_]+=.*\s#", line)
    ]
    assert bad == []


def test_the_daily_ai_call_cap_is_documented():
    assert "EBB_DAILY_AI_CALLS" in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_env_example_lists_new_settings():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in (
        "EBB_TEACH_MODEL",
        "EBB_QUIZ_MODEL",
        "EBB_EVAL_WRITER_MODEL",
        "EBB_TEACH_BACKUP",
        "EBB_QUIZ_BACKUP",
        "OPENROUTER_API_KEY",
        "EBB_SHOW_COSTS",
    ):
        assert name in text
