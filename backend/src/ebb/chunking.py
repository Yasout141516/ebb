"""Splitting extracted notes into passages with page numbers and headings (pure)."""

import re
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from ebb.domain import ChunkDraft

if TYPE_CHECKING:
    from ebb.notes import ExtractedNotes

TARGET = 1200
MAX = 2000
OVERLAP = 200

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MD_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_SEP = "\n\n"

# (text, heading, 1-based page or None)
_Unit = tuple[str, str | None, int | None]
# (page, offset in some text) where that page starts in it
_Starts = list[tuple[int, int]]


def chunk_notes(notes: ExtractedNotes) -> tuple[ChunkDraft, ...]:
    units: list[_Unit] = []
    for text, heading, page in _paragraphs(notes):
        units.extend((piece, heading, page) for piece in _split_long(text))
    return tuple(_pack(units))


def _paragraphs(notes: ExtractedNotes) -> Iterator[_Unit]:
    if notes.pages is not None:
        heading: str | None = None
        for number, page in enumerate(notes.pages, start=1):
            first = next((ln.strip() for ln in page.splitlines() if ln.strip()), "")
            if first and len(first) <= 80 and not first.endswith("."):
                heading = first
            for para in _blank_split(page):
                yield para, heading, number
    elif notes.kind == "md":
        yield from _markdown_paragraphs(notes.text)
    else:
        for para in _blank_split(notes.text):
            yield para, None, None


def _markdown_paragraphs(text: str) -> Iterator[_Unit]:
    heading: str | None = None
    para_heading: str | None = None
    lines: list[str] = []
    for line in [*text.splitlines(), ""]:
        match = _MD_HEADING.match(line.strip())
        if (not line.strip() or match) and lines:
            yield "\n".join(lines), para_heading, None
            lines = []
        if match:
            heading = match.group(1) or None
        if line.strip():
            if not lines:
                para_heading = heading
            lines.append(line.strip())


def _blank_split(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_long(text: str) -> list[str]:
    """Return the paragraph whole, or pieces of at most TARGET characters if over MAX."""
    if len(text) <= MAX:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > TARGET:  # no sentence end in reach: hard split
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:TARGET])
            sentence = sentence[TARGET:]
        if current and len(current) + 1 + len(sentence) > TARGET:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


def _overlap(content: str) -> str:
    """Up to OVERLAP characters from the end of content, starting at a sentence if possible."""
    if len(content) <= OVERLAP:
        return content
    tail = content[-OVERLAP:]
    match = _SENTENCE_END.search(tail)
    if match:
        return tail[match.end() :]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail


def _carry(draft: ChunkDraft) -> tuple[str, _Starts]:
    """The overlap taken from the end of `draft`, with where each of its pages starts in it."""
    text = _overlap(draft.content)
    if draft.page_start is None:
        return text, []
    begin = len(draft.content) - len(text)
    pages = [(draft.page_start + n, offset) for n, offset in enumerate(draft.page_offsets)]
    first = max(page for page, offset in pages if offset <= begin)  # the first offset is 0
    return text, [(first, 0), *((page, offset - begin) for page, offset in pages if offset > begin)]


def _pack(units: Sequence[_Unit]) -> Iterator[ChunkDraft]:
    parts: list[tuple[str, int | None]] = []
    size = 0
    heading: str | None = None
    carry: tuple[str, _Starts] = ("", [])
    ordinal = 0
    for text, unit_heading, page in units:
        changed = bool(parts) and unit_heading != heading
        full = bool(parts) and size + len(_SEP) + len(text) > TARGET
        if changed or full:
            draft = _build(ordinal, parts, heading, carry)
            yield draft
            ordinal += 1
            carry = ("", []) if changed else _carry(draft)
            parts, size = [], 0
        if not parts:
            heading = unit_heading
        size += len(text) + (len(_SEP) if parts else 0)
        parts.append((text, page))
    if parts:
        yield _build(ordinal, parts, heading, carry)


def _build(
    ordinal: int,
    parts: list[tuple[str, int | None]],
    heading: str | None,
    carry: tuple[str, _Starts],
) -> ChunkDraft:
    own = _SEP.join(text for text, _ in parts)
    # The overlap is dropped rather than letting a chunk pass MAX.
    prefix, carried = carry if len(carry[0]) + len(_SEP) + len(own) <= MAX else ("", [])
    content = f"{prefix}{_SEP}{own}" if prefix else own
    position = len(content) - len(own)
    # The overlap keeps the pages it came from: a chunk may start on an earlier page.
    starts: _Starts = list(carried) if prefix else []
    for text, page in parts:
        if page is not None:
            starts.append((page, position))
        position += len(text) + len(_SEP)
    return ChunkDraft(
        ordinal=ordinal,
        content=content,
        heading=heading,
        page_start=starts[0][0] if starts else None,
        page_end=starts[-1][0] if starts else None,
        page_offsets=_page_offsets(starts),
    )


def _page_offsets(starts: list[tuple[int, int]]) -> tuple[int, ...]:
    """One offset per page from the first to the last page the chunk covers."""
    if not starts:
        return ()
    offsets = [
        next(offset for page, offset in starts if page >= number)
        for number in range(starts[0][0], starts[-1][0] + 1)
    ]
    offsets[0] = 0
    return tuple(offsets)


def digest(chunks: Sequence[ChunkDraft], limit: int = 30_000) -> str:
    """The distinct headings in order, then the start of chunks sampled across the document."""
    head = "\n".join(dict.fromkeys(c.heading for c in chunks if c.heading))
    count = max(1, (limit - len(head) - 1) // 301)
    if count >= len(chunks):
        picked = list(chunks)
    elif count == 1:
        picked = [chunks[0]]
    else:
        step = (len(chunks) - 1) / (count - 1)
        picked = [chunks[round(i * step)] for i in range(count)]
    body = "\n".join(c.content[:300] for c in picked)
    return f"{head}\n{body}"[:limit]
