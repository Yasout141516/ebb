from ebb.chunking import MAX, OVERLAP, TARGET, chunk_notes, digest
from ebb.domain import ChunkDraft
from ebb.notes import ExtractedNotes


def notes(text="", kind="txt", pages=None):
    return ExtractedNotes("n." + kind, kind, len(text), "x", pages, text)


def paragraph(n: int) -> str:
    return f"Paragraph {n} starts here. " + "It talks about routing tables. " * 12


def test_paragraphs_pack_to_target_with_overlap():
    text = "\n\n".join(paragraph(n) for n in range(30))
    chunks = chunk_notes(notes(text))
    assert len(chunks) > 3
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(len(c.content) <= MAX for c in chunks)
    for a, b in zip(chunks, chunks[1:], strict=False):
        shared = next(
            (
                k
                for k in range(min(len(a.content), len(b.content), OVERLAP), 0, -1)
                if b.content.startswith(a.content[-k:])
            ),
            0,
        )
        assert 0 < shared <= OVERLAP
        assert len(a.content) <= TARGET + OVERLAP + 2


def test_long_paragraph_splits_at_sentences():
    sentence = "The router forwards each packet by its destination address."
    text = " ".join([sentence] * 80)
    assert len(text) > MAX
    chunks = chunk_notes(notes(text))
    assert len(chunks) > 1
    assert all(len(c.content) <= MAX for c in chunks)
    assert all(c.content.rstrip().endswith(".") for c in chunks)


def test_long_paragraph_without_sentence_ends_is_hard_split():
    chunks = chunk_notes(notes("x" * 5000))
    assert len(chunks) > 1 and all(len(c.content) <= MAX for c in chunks)


def test_markdown_heading_change_starts_a_new_chunk():
    text = (
        "# Addressing\n\nIP addresses identify hosts.\n\n"
        "## Subnet masks\nA mask splits network and host bits.\n\nMore on masks."
    )
    chunks = chunk_notes(notes(text, kind="md"))
    assert [c.heading for c in chunks] == ["Addressing", "Subnet masks"]
    assert "Subnet masks" in chunks[1].content
    assert "IP addresses" not in chunks[1].content


def test_pdf_chunk_spanning_pages_records_offsets():
    pages = (
        "Intro\n\nHello there.",
        "Still intro text.",
        "Routing basics\n\nPage three text.",
        "More about routing on page four.",
    )
    chunks = chunk_notes(notes("\n\n".join(pages), kind="pdf", pages=pages))
    spanning = next(c for c in chunks if c.heading == "Routing basics")
    assert (spanning.page_start, spanning.page_end) == (3, 4)
    assert spanning.page_offsets[0] == 0
    assert spanning.page_offsets[1] == spanning.content.index("More about")


def test_text_chunks_have_no_pages_or_heading():
    chunk = chunk_notes(notes("just text"))[0]
    assert chunk.heading is None and chunk.page_start is None
    assert chunk.page_offsets == ()


def test_digest_is_capped_and_samples_the_whole_document():
    chunks = tuple(
        ChunkDraft(i, f"chunk-{i} " + "word " * 400, f"H{i // 10}", None, None, ())
        for i in range(200)
    )
    result = digest(chunks)
    assert len(result) <= 30_000
    assert result.startswith("H0\nH1")
    assert "chunk-0 " in result and "chunk-199 " in result
