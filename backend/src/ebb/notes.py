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
from ebb.units import format_megabytes

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
    kind = _kind(path.name)
    size = path.stat().st_size
    check_size(path.name, size)
    try:
        data = path.read_bytes()
    except OSError:
        raise NotesError(f"Can't open {path}.") from None
    return _extract(path.name, kind, data, f"Can't open {path}.")


def read_notes_bytes(filename: str, data: bytes) -> ExtractedNotes:
    """The same checks and messages as `read_notes`, for an upload already in memory."""
    kind = _kind(filename)
    check_size(filename, len(data))
    return _extract(filename, kind, data, f"Can't open {filename}.")


def check_upload(filename: str, size: int) -> None:
    """The checks that need only the name and size, for an upload too big to read whole."""
    _kind(filename)
    check_size(filename, size)


def _kind(filename: str) -> str:
    kind = KINDS.get(Path(filename).suffix.lower())
    if kind is None:
        raise NotesError("Ebb reads PDF, TXT or MD files.")
    return kind


def check_size(filename: str, size: int) -> None:
    """Refuses a file over MAX_BYTES; also for an upload known only by its size."""
    if size > MAX_BYTES:
        raise NotesError(
            f"{filename} is {format_megabytes(size)} — over the 20 MB limit. "
            "Split it into chapters, or upload just the pages you're studying."
        )


def _extract(filename: str, kind: str, data: bytes, unopenable: str) -> ExtractedNotes:
    pages: tuple[str, ...] | None = None
    if kind == "pdf":
        pages = _pdf_pages(data, unopenable)
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
            raise NotesError(f"Couldn't read {filename} as text — save it as UTF-8.") from None
        if not text.strip():
            raise NotesError(f"{filename} has no text in it.")
    return ExtractedNotes(
        filename=filename,
        kind=kind,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        pages=pages,
        text=text,
    )


def fold_ligatures(text: str) -> str:
    return text.translate(_LIGATURES)


def _pdf_pages(data: bytes, unopenable: str) -> tuple[str, ...]:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return tuple(fold_ligatures(page.extract_text() or "") for page in reader.pages)
    except _DAMAGED_PDF:
        raise NotesError(unopenable) from None


def _is_scanned(pages: tuple[str, ...]) -> bool:
    if not pages:
        return True
    return sum(len(p.strip()) for p in pages) / len(pages) < MIN_CHARS_PER_PAGE


def prepare(path: Path) -> NewDocument:
    return _document(read_notes(path))


def prepare_bytes(filename: str, data: bytes) -> NewDocument:
    return _document(read_notes_bytes(filename, data))


def _document(notes: ExtractedNotes) -> NewDocument:
    return NewDocument(
        filename=notes.filename,
        kind=notes.kind,
        size_bytes=notes.size_bytes,
        page_count=len(notes.pages) if notes.pages is not None else None,
        sha256=notes.sha256,
        chunks=chunk_notes(notes),
    )
