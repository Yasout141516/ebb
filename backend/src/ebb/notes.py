"""Reading uploaded notes: the upload checks and text extraction (pure, no database)."""

import hashlib
import io
import zlib
from dataclasses import dataclass
from pathlib import Path

import pypdf
from pypdf.errors import PyPdfError

from ebb.chunking import chunk_notes
from ebb.domain import NewDocument
from ebb.ui import format_megabytes

MAX_BYTES = 20 * 1024 * 1024
MIN_CHARS_PER_PAGE = 50
KINDS = {".pdf": "pdf", ".txt": "txt", ".md": "md"}

# pypdf keeps typographic ligatures ("ﬁ"), and Postgres full-text search doesn't match them
# to "fi". Only these are folded: full NFKC would also turn "2¹⁰" into "210".
_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)

# pypdf raises these, not only PyPdfError, on damaged real-world files.
_DAMAGED_PDF = (
    PyPdfError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    zlib.error,
    RecursionError,
    OSError,
)


class NotesError(Exception):
    """A refusal; str() is the exact message shown to the user."""


@dataclass(frozen=True)
class ExtractedNotes:
    filename: str
    kind: str
    size_bytes: int
    sha256: str
    pages: tuple[str, ...] | None  # PDF only
    text: str


def read_notes(path: Path) -> ExtractedNotes:
    if not path.is_file():
        raise NotesError(f"Can't open {path}.")
    kind = KINDS.get(path.suffix.lower())
    if kind is None:
        raise NotesError("Ebb reads PDF, TXT or MD files.")
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise NotesError(
            f"{path.name} is {format_megabytes(size)} — over the 20 MB limit. "
            "Split it into chapters, or upload just the pages you're studying."
        )
    try:
        data = path.read_bytes()
    except OSError:
        raise NotesError(f"Can't open {path}.") from None
    pages: tuple[str, ...] | None = None
    if kind == "pdf":
        pages = _pdf_pages(path, data)
        text = "\n\n".join(pages)
        # An image-only PDF has no text at all, so this also covers "empty" for PDFs.
        if _is_scanned(pages):
            raise NotesError(
                "No text found — this looks like a scanned PDF. Export it as text, "
                "or upload the PDF your slides were made from."
            )
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise NotesError(f"Couldn't read {path.name} as text — save it as UTF-8.") from None
        if not text.strip():
            raise NotesError(f"{path.name} has no text in it.")
    return ExtractedNotes(
        filename=path.name,
        kind=kind,
        size_bytes=size,
        sha256=hashlib.sha256(data).hexdigest(),
        pages=pages,
        text=text,
    )


def fold_ligatures(text: str) -> str:
    return text.translate(_LIGATURES)


def _pdf_pages(path: Path, data: bytes) -> tuple[str, ...]:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return tuple(fold_ligatures(page.extract_text() or "") for page in reader.pages)
    except _DAMAGED_PDF:
        raise NotesError(f"Can't open {path}.") from None


def _is_scanned(pages: tuple[str, ...]) -> bool:
    if not pages:
        return True
    return sum(len(p.strip()) for p in pages) / len(pages) < MIN_CHARS_PER_PAGE


def prepare(path: Path) -> NewDocument:
    notes = read_notes(path)
    return NewDocument(
        filename=notes.filename,
        kind=notes.kind,
        size_bytes=notes.size_bytes,
        page_count=len(notes.pages) if notes.pages is not None else None,
        sha256=notes.sha256,
        chunks=chunk_notes(notes),
    )
