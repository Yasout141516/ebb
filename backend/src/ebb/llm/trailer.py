"""A learn-phase reply is markdown, then a trailer the code reads and the learner never sees:

    <<<EBB
    {"citations": [{"source": "N1", "quote": "..."}], "suggestions": [...], "checks": [...]}
    EBB>>>

Only the last `<<<EBB` block ends the markdown, so a marker earlier in the reply is text.
Models often wrap the trailer, or its JSON, in a code fence; those fences are dropped. A
reply with no marker at all may end with the bare object instead, which is read as the
trailer; the streamed text then shows it until the reply's turn replaces that text."""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.exceptions import OutputParserException

OPEN, CLOSE = "<<<EBB", "EBB>>>"
FENCE = "```"
TRAILER_KEYS = {"citations", "suggestions", "checks"}


def _is_fence(line: str) -> bool:
    return line.strip().startswith(FENCE)


def _unfenced(text: str) -> str:
    """`text` without a fence line opening it or closing it."""
    lines = text.strip().split("\n")
    if lines and _is_fence(lines[0]):
        lines = lines[1:]
    if lines and _is_fence(lines[-1]):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _opens_fence(text: str, start: int) -> bool:
    """Whether the line starting at `start` opens a code fence (an even number before it)."""
    return sum(_is_fence(line) for line in text[:start].split("\n")) % 2 == 0


def _markdown_before(text: str, start: int) -> str:
    """The reply's markdown: all before the trailer, less a fence line opened to wrap it."""
    markdown = text[:start].rstrip()
    last = markdown.rfind("\n") + 1
    if _is_fence(markdown[last:]) and _opens_fence(markdown, last):  # it fences the trailer
        markdown = markdown[:last]
    return markdown.strip()


def _unmarked(text: str) -> tuple[str, dict[str, Any]]:
    """A reply whose trailer came without its markers (seen live from gpt-oss): a JSON
    object of only the trailer's keys, starting a line, ends it."""
    body = text.rstrip()
    if body.endswith(FENCE):  # the fence closing a fenced object
        body = body[: -len(FENCE)].rstrip()
    start = len(body)
    while (start := body.rfind("{", 0, start)) >= 0:
        if start == 0 or body[start - 1] == "\n":
            try:
                data = json.loads(body[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data and set(data) <= TRAILER_KEYS:
                return _markdown_before(text, start), data
    raise OutputParserException(f"the reply has no {OPEN} trailer")


def split(text: str) -> tuple[str, dict[str, Any]]:
    """The markdown and the trailer's object. A reply without one closing it is invalid."""
    start = text.rfind(OPEN)
    if start < 0:
        return _unmarked(text)
    body = text[start + len(OPEN) :].strip()
    if body.endswith(FENCE):  # the fence closing a fenced trailer
        body = body[: -len(FENCE)].rstrip()
    if not body.endswith(CLOSE):
        raise OutputParserException(f"the trailer does not end the reply with {CLOSE}")
    try:
        data = json.loads(_unfenced(body[: -len(CLOSE)]))
    except json.JSONDecodeError as error:
        raise OutputParserException(f"the trailer is not JSON: {error}") from error
    if not isinstance(data, dict):
        raise OutputParserException("the trailer is not a JSON object")
    return _markdown_before(text, start), data


def _held(text: str) -> int:
    """How much of the end of `text` could still turn out to start a marker."""
    for size in range(len(OPEN) - 1, 0, -1):
        if text.endswith(OPEN[:size]):
            return size
    return 0


def _fence_held(text: str, end: int) -> int:
    """Where text[:end] should stop instead, so that a code fence opened on its last line,
    which may turn out to wrap the trailer, is held back too."""
    last = end
    while last > 0 and text[last - 1].isspace():
        last -= 1
    start = text.rfind("\n", 0, last) + 1
    line = text[start:last].strip()
    could_be_fence = line.startswith(FENCE) or (line != "" and FENCE.startswith(line))
    return start if could_be_fence and _opens_fence(text, start) else end


class Streamer:
    """Passes a streamed reply's markdown on to `on_text` as it arrives, never the trailer.
    It holds back everything from the last marker seen (a later one releases it), an end
    that could still become a marker, and a code fence opened on the last line, so what it
    passes on is what `split` reads as the markdown, give or take surrounding whitespace.
    A stray marker early in the reply holds back all that follows it, which then appears
    in one burst when a later marker releases it."""

    def __init__(self, on_text: Callable[[str], None]) -> None:
        self.on_text = on_text
        self.text = ""  # the reply so far
        self.sent = 0  # how much of it has been passed on

    def __call__(self, chunk: str) -> None:
        self.text += chunk
        start = self.text.rfind(OPEN, self.sent)
        end = start if start >= 0 else len(self.text) - _held(self.text)
        end = _fence_held(self.text, end)
        if end > self.sent:
            self.on_text(self.text[self.sent : end])
            self.sent = end
