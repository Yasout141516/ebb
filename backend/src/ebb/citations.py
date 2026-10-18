"""Checking that a cited quote really appears in the named source (pure)."""

import re
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from ebb.domain import Chunk, Citation, Claim, Source
from ebb.web import WebPage

MAX_CITATIONS = 3
MAX_QUOTE = 200
MAX_RETRIES = 2

_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_HYPHEN_BREAK = re.compile(r"-[ \t]*\r?\n\s*")


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise `text` and return, for each output character, its index in `text`."""
    text = text.translate(_TYPOGRAPHY)
    kept = list(range(len(text)))
    # Drop each "-<line break><indent>" run so the two halves of a word join.
    for match in reversed(list(_HYPHEN_BREAK.finditer(text))):
        del kept[match.start() : match.end()]
    chars: list[str] = []
    index: list[int] = []
    for i in kept:
        for ch in text[i].casefold():
            if ch.isspace():
                if chars and chars[-1] != " ":
                    chars.append(" ")
                    index.append(i)
            else:
                chars.append(ch)
                index.append(i)
    if chars and chars[-1] == " ":
        chars.pop()
        index.pop()
    return "".join(chars), index


def normalise(text: str) -> str:
    return _normalise_with_map(text)[0]


def note_sources(chunks: Sequence[Chunk]) -> list[Source]:
    return [
        Source(
            f"N{n}",
            "chunk",
            str(c.id),
            c.filename,
            c.content,
            c.heading,
            c.page_start,
            c.page_offsets,
        )
        for n, c in enumerate(chunks, 1)
    ]


def web_sources(pages: Sequence[WebPage]) -> list[Source]:
    sources = []
    for n, page in enumerate(pages, 1):
        domain = (urlparse(page.url).hostname or "").removeprefix("www.")
        sources.append(Source(f"W{n}", "web", page.url, f"{domain} · {page.title}", page.text))
    return sources


def display_label(source: Source, page: int | None) -> str:
    if source.kind == "web":
        return source.title
    if page is not None:
        return f"{source.title} · p. {page}"
    if source.heading:
        return f"{source.title} · § {source.heading}"
    return source.title


def page_of(source: Source, quote: str) -> int | None:
    """The page holding the quote's first character, or None without page data."""
    if source.page_start is None or not source.page_offsets:
        return None
    text, index = _normalise_with_map(source.text)
    at = text.find(normalise(quote))
    if at < 0 or not index:
        return None
    original = index[at]
    return source.page_start + max(bisect_right(source.page_offsets, original) - 1, 0)


def verify(
    claims: Sequence[Claim], sources: Sequence[Source]
) -> tuple[tuple[Citation, ...], tuple[str, ...]]:
    """Keep the claims whose quote is in the named source; say why the rest failed."""
    by_label = {s.label: s for s in sources}
    normalised: dict[str, str] = {}
    valid: list[Citation] = []
    failures: list[str] = []
    for claim in claims:
        source = by_label.get(claim.source)
        quote = claim.quote.strip()
        if source is None:
            failures.append(f"{claim.source} is not one of the sources you were given")
        elif not normalise(quote):
            failures.append(f"the quote from {claim.source} is empty")
        elif len(quote) > MAX_QUOTE:
            failures.append(f"the quote from {claim.source} is longer than {MAX_QUOTE} characters")
        else:
            haystack = normalised.setdefault(source.label, normalise(source.text))
            if normalise(quote) not in haystack:
                failures.append(f"the quote from {claim.source} is not in that source")
                continue
            page = page_of(source, quote)
            valid.append(
                Citation(source.kind, source.ref, display_label(source, page), page, quote)
            )
    return tuple(valid[:MAX_CITATIONS]), tuple(failures)


def to_dict(c: Citation) -> dict[str, Any]:
    return {"kind": c.kind, "ref": c.ref, "label": c.label, "page": c.page, "quote": c.quote}


def from_dict(d: Mapping[str, Any]) -> Citation:
    return Citation(d["kind"], d["ref"], d["label"], d["page"], d["quote"])


def source_to_dict(s: Source) -> dict[str, Any]:
    return {
        "label": s.label,
        "kind": s.kind,
        "ref": s.ref,
        "title": s.title,
        "text": s.text,
        "heading": s.heading,
        "page_start": s.page_start,
        "page_offsets": list(s.page_offsets),
    }


def source_from_dict(d: Mapping[str, Any]) -> Source:
    return Source(
        d["label"],
        d["kind"],
        d["ref"],
        d["title"],
        d["text"],
        d["heading"],
        d["page_start"],
        tuple(d["page_offsets"]),
    )
