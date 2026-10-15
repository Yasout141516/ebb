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
