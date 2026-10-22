import json
from uuid import uuid4

from ebb.citations import (
    MAX_CITATIONS,
    display_label,
    from_dict,
    normalise,
    note_sources,
    page_of,
    source_from_dict,
    source_to_dict,
    to_dict,
    verify,
    web_sources,
)
from ebb.domain import Chunk, Citation, Claim, Source
from ebb.web import WebPage

N1 = Source("N1", "chunk", "id1", "a.md", "Subnet masks split an address into network and host.")
N2 = Source("N2", "chunk", "id2", "b.md", "ARP resolves an IP address to a MAC address.")


def test_normalise():
    assert normalise("  “Hi”  —  it’s\n\tnet-\nwork  ") == '"hi" - it\'s network'


def test_unknown_label_fails():
    cites, fails = verify([Claim("N5", "anything")], [N1, N2])
    assert cites == () and fails == ("N5 is not one of the sources you were given",)


def test_quote_must_be_in_the_named_source():
    cites, fails = verify([Claim("N1", "ARP resolves an IP address")], [N1, N2])
    assert cites == () and fails == ("the quote from N1 is not in that source",)


def test_verify_tolerates_pdf_line_breaks_and_typography():
    src = Source(
        "N1",
        "chunk",
        "id",
        "a.pdf",
        "The first address identifies the net-\nwork and the “last” is the broadcast.",
    )
    cites, fails = verify([Claim("N1", 'identifies the network and the "last" is')], [src])
    assert len(cites) == 1 and fails == ()


def test_verify_folds_ligatures_in_stored_chunks():
    # Chunks stored before extraction folded ligatures still verify: casefold() turns "ﬁ" into "fi".
    text = "Intro page. Beneﬁts of MVC are ﬂexible."
    src = Source("N1", "chunk", "id", "a.pdf", text, page_start=4, page_offsets=(0, 12))
    cites, fails = verify([Claim("N1", "Benefits of MVC are flexible")], [src])
    assert fails == () and [c.page for c in cites] == [5]


def test_quote_split_across_chunks_fails():
    cites, fails = verify([Claim("N1", "network and host. ARP resolves")], [N1, N2])
    assert cites == () and len(fails) == 1


def test_empty_and_overlong_quotes_fail():
    long = Source("N1", "chunk", "i", "a.md", "word " * 100)
    cites, fails = verify([Claim("N1", "  "), Claim("N1", "word " * 50)], [long])
    assert cites == ()
    assert fails == (
        "the quote from N1 is empty",
        "the quote from N1 is longer than 200 characters",
    )


def test_at_most_three_citations_kept():
    cites, fails = verify([Claim("N1", "Subnet masks")] * 5, [N1])
    assert len(cites) == MAX_CITATIONS and fails == ()


def test_valid_citation_fields():
    src = Source(
        "N1",
        "chunk",
        "cid",
        "CSE321.pdf",
        "x" * 45 + "target text",
        page_start=12,
        page_offsets=(0, 40),
    )
    (c,), _ = verify([Claim("N1", " target text ")], [src])
    assert c == Citation("chunk", "cid", "CSE321.pdf · p. 13", 13, "target text")


def test_page_of_uses_offsets():
    src = Source(
        "N1", "chunk", "i", "a.pdf", "x" * 45 + "target", page_start=12, page_offsets=(0, 40)
    )
    assert page_of(src, "target") == 13
    assert page_of(src, "xxx") == 12
    assert page_of(N1, "Subnet") is None


def test_page_of_maps_back_through_normalisation():
    text = "a" * 38 + " net-\nwork starts here"
    src = Source("N1", "chunk", "i", "a.pdf", text, page_start=1, page_offsets=(0, 39))
    assert page_of(src, "network starts") == 2


def test_display_labels():
    pdf = Source("N1", "chunk", "i", "CSE321.pdf", "t", page_start=12, page_offsets=(0,))
    md = Source("N2", "chunk", "i", "networks.md", "t", heading="Subnet masks")
    plain = Source("N3", "chunk", "i", "n.txt", "t")
    web = Source("W1", "web", "u", "cloudflare.com · What is a subnet?", "t")
    assert display_label(pdf, 12) == "CSE321.pdf · p. 12"
    assert display_label(md, None) == "networks.md · § Subnet masks"
    assert display_label(plain, None) == "n.txt"
    assert display_label(web, None) == "cloudflare.com · What is a subnet?"


def test_note_and_web_sources():
    cid = uuid4()
    chunk = Chunk(cid, "a.pdf", "text", "H", 3, (0, 10))
    (n,) = note_sources([chunk])
    assert n == Source("N1", "chunk", str(cid), "a.pdf", "text", "H", 3, (0, 10))
    pages = [
        WebPage("https://www.cloudflare.com/x", "What?", "body"),
        WebPage("https://b.org/y", "T", "z"),
    ]
    w = web_sources(pages)
    assert [s.label for s in w] == ["W1", "W2"]
    assert w[0] == Source(
        "W1", "web", "https://www.cloudflare.com/x", "cloudflare.com · What?", "body"
    )


def test_round_trips_through_json():
    c = Citation("chunk", "i", "a.pdf · p. 2", 2, "q")
    assert from_dict(json.loads(json.dumps(to_dict(c)))) == c
    s = Source("N1", "chunk", "i", "a.pdf", "t", "H", 3, (0, 10))
    assert source_from_dict(json.loads(json.dumps(source_to_dict(s)))) == s
