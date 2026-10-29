"""The trailer after a learn-phase reply's markdown: streamed past, then parsed."""

import pytest
from langchain_core.exceptions import OutputParserException

from ebb.llm import trailer

TRAILER = (
    '<<<EBB\n{"citations": [{"source": "N1", "quote": "Resolvers cache answers"}], '
    '"suggestions": ["What is a TTL?", "Who runs the root servers?"], '
    '"checks": ["Why cache at all?", "What goes stale?"]}\nEBB>>>'
)


def streamed(*chunks: str) -> list[str]:
    pieces: list[str] = []
    streamer = trailer.Streamer(pieces.append)
    for chunk in chunks:
        streamer(chunk)
    return pieces


def test_streamer_never_forwards_the_trailer():
    # The marker arrives split across three chunks.
    pieces = streamed(
        "# DNS\n\nNames",
        " resolve.\n<",
        "<<E",
        'BB\n{"citations": [], "suggestions": [],',
        ' "checks": []}\nEBB>>>',
    )
    assert pieces == ["# DNS\n\nNames", " resolve.\n"]


def test_streamer_releases_held_text_that_is_not_a_marker():
    assert streamed("x <", "= y <<", "< z", " <<<EB", "C done") == [
        "x ",
        "<= y ",
        "<<< z",
        " ",
        "<<<EBC done",
    ]


def test_a_later_marker_releases_an_earlier_one():
    """Only the last block is the trailer, so text held after an earlier marker is shown
    once a later one appears: the screen ends where `split` ends the markdown."""
    chunks = ["Intro\n<<<EBB\n", "not the end\nEBB>>>\nMore", "\n" + TRAILER]
    pieces = streamed(*chunks)
    assert pieces == ["Intro\n", "<<<EBB\nnot the end\nEBB>>>\nMore\n"]
    markdown, _ = trailer.split("".join(chunks))
    assert "".join(pieces).strip() == markdown


def test_split_reads_citations_suggestions_checks():
    markdown, data = trailer.split("\n# DNS\n\nNames resolve.\n\n" + TRAILER + "\n")
    assert markdown == "# DNS\n\nNames resolve."
    assert data == {
        "citations": [{"source": "N1", "quote": "Resolvers cache answers"}],
        "suggestions": ["What is a TTL?", "Who runs the root servers?"],
        "checks": ["Why cache at all?", "What goes stale?"],
    }


def test_split_reads_the_last_block():
    markdown, data = trailer.split('A\n<<<EBB\n{"checks": ["early"]}\nEBB>>>\nB\n' + TRAILER)
    assert markdown == 'A\n<<<EBB\n{"checks": ["early"]}\nEBB>>>\nB'
    assert data["checks"] == ["Why cache at all?", "What goes stale?"]


@pytest.mark.parametrize(
    "reply",
    [
        "# DNS\n\nNo trailer at all.",
        "# DNS\n<<<EBB\n{}",  # never closed
        "# DNS\n<<<EBB\n{not json}\nEBB>>>",
        '# DNS\n<<<EBB\n["a list"]\nEBB>>>',
        "# DNS\n<<<EBB\n{}\nEBB>>>\nand then more text",
    ],
)
def test_missing_trailer_is_a_parse_error(reply):
    with pytest.raises(OutputParserException):
        trailer.split(reply)
