"""A learn-phase reply is markdown, then a trailer the code reads and the learner never sees:

    <<<EBB
    {"citations": [{"source": "N1", "quote": "..."}], "suggestions": [...], "checks": [...]}
    EBB>>>

Only the last `<<<EBB` block ends the markdown, so a marker earlier in the reply is text."""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.exceptions import OutputParserException

OPEN, CLOSE = "<<<EBB", "EBB>>>"


def split(text: str) -> tuple[str, dict[str, Any]]:
    """The markdown and the trailer's object. A reply without one closing it is invalid."""
    start = text.rfind(OPEN)
    if start < 0:
        raise OutputParserException(f"the reply has no {OPEN} trailer")
    body = text[start + len(OPEN) :].strip()
    if not body.endswith(CLOSE):
        raise OutputParserException(f"the trailer does not end the reply with {CLOSE}")
    try:
        data = json.loads(body[: -len(CLOSE)])
    except json.JSONDecodeError as error:
        raise OutputParserException(f"the trailer is not JSON: {error}") from error
    if not isinstance(data, dict):
        raise OutputParserException("the trailer is not a JSON object")
    return text[:start].strip(), data


def _held(text: str) -> int:
    """How much of the end of `text` could still turn out to start a marker."""
    for size in range(len(OPEN) - 1, 0, -1):
        if text.endswith(OPEN[:size]):
            return size
    return 0


class Streamer:
    """Passes a streamed reply's markdown on to `on_text` as it arrives, never the trailer.
    It holds back everything from the last marker seen (a later one releases it) and an end
    that could still become a marker, so what it passes on is what `split` reads as the
    markdown, give or take surrounding whitespace."""

    def __init__(self, on_text: Callable[[str], None]) -> None:
        self.on_text = on_text
        self.text = ""  # the reply so far
        self.sent = 0  # how much of it has been passed on

    def __call__(self, chunk: str) -> None:
        self.text += chunk
        start = self.text.rfind(OPEN, self.sent)
        end = start if start >= 0 else len(self.text) - _held(self.text)
        if end > self.sent:
            self.on_text(self.text[self.sent : end])
            self.sent = end
