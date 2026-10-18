import hashlib

import pytest

from ebb.notes import MAX_BYTES, NotesError, prepare, read_notes
from tests.pdfs import build_pdf, slide_page, text_page

LONG = "Subnetting splits a network into smaller networks. " * 3


def test_read_notes_accepts_upper_case_extension_and_spaces(tmp_path):
    path = build_pdf(tmp_path / "My Notes.PDF", [text_page(LONG)])
    notes = read_notes(path)
    assert notes.kind == "pdf"
    assert notes.filename == "My Notes.PDF"
    assert notes.pages is not None and len(notes.pages) == 1


def test_read_notes_rejects_directories_and_missing_files(tmp_path):
    for path in (tmp_path, tmp_path / "nope.pdf"):
        with pytest.raises(NotesError) as err:
            read_notes(path)
        assert str(err.value) == f"Can't open {path}."


def test_broken_pdf_cannot_be_opened(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(NotesError) as err:
        read_notes(path)
    assert str(err.value) == f"Can't open {path}."


def test_wrong_type_is_refused(tmp_path):
    path = tmp_path / "slides.pptx"
    path.write_bytes(b"x")
    with pytest.raises(NotesError, match=r"^Ebb reads PDF, TXT or MD files\.$"):
        read_notes(path)


def test_size_limit(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"a" * (MAX_BYTES + 1))
    with pytest.raises(NotesError) as err:
        read_notes(path)
    assert str(err.value) == (
        "big.txt is 20.0 MB — over the 20 MB limit. "
        "Split it into chapters, or upload just the pages you're studying."
    )


def test_scanned_pdf_is_refused(tmp_path):
    path = build_pdf(tmp_path / "scan.pdf", ["image", "image"])
    with pytest.raises(NotesError) as err:
        read_notes(path)
    assert str(err.value) == (
        "No text found — this looks like a scanned PDF. Export it as text, "
        "or upload the PDF your slides were made from."
    )


def test_slide_deck_with_blank_pages_is_accepted_and_chunked(tmp_path):
    titles = [f"Slide {n}" for n in range(1, 11)]
    layout: list[list[str] | str] = []
    slide_pages = []
    for number, title in enumerate(titles, start=1):
        if number in (3, 6):
            layout.append("")
        elif number == 9:
            layout.append("image")
        else:
            layout.append(
                slide_page(
                    title,
                    f"{title} covers the first topic in detail.",
                    f"{title} covers the second topic in detail.",
                    f"{title} covers the third topic in detail.",
                )
            )
            slide_pages.append(number)
    path = build_pdf(tmp_path / "deck.pdf", layout)
    doc = prepare(path)
    assert doc.kind == "pdf" and doc.page_count == 10
    assert all(c.content.strip() for c in doc.chunks)
    assert [c.heading for c in doc.chunks] == [titles[n - 1] for n in slide_pages]
    assert [c.page_start for c in doc.chunks] == slide_pages
    assert [c.page_end for c in doc.chunks] == slide_pages


def test_txt_utf8_with_bom_and_bad_encoding(tmp_path):
    good = tmp_path / "good.txt"
    good.write_bytes(b"\xef\xbb\xbfhello world")
    assert read_notes(good).text == "hello world"
    plain = tmp_path / "plain.txt"
    plain.write_text("héllo", encoding="utf-8")
    assert read_notes(plain).text == "héllo"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(NotesError) as err:
        read_notes(bad)
    assert str(err.value) == "Couldn't read bad.txt as text — save it as UTF-8."


def test_empty_text_is_refused(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(NotesError) as err:
        read_notes(path)
    assert str(err.value) == "notes.md has no text in it."


def test_sha256_is_of_the_file_bytes(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"\xef\xbb\xbfsome notes")
    assert read_notes(path).sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_prepare_text_file_has_no_page_count(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("some notes", encoding="utf-8")
    doc = prepare(path)
    assert doc.page_count is None and len(doc.chunks) == 1
