import pytest

from ebb.repeats import too_close


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (
            "What is the broadcast address of 192.168.1.0/24?",
            "Which address is the broadcast address for 192.168.1.0/24?",
        ),
        (
            "Why are two addresses unusable in every subnet?",
            "Explain why each subnet loses two usable addresses.",
        ),
    ],
)
def test_paraphrases_are_too_close(new, old):
    assert too_close(new, [old])


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (
            "What is the broadcast address of 192.168.1.0/24?",
            "How many usable hosts does a /26 subnet have?",
        ),
        ("What does ARP resolve?", "What does DNS resolve?"),
    ],
)
def test_different_questions_are_not(new, old):
    assert not too_close(new, [old])


def test_containment_needs_four_words():
    assert too_close("subnet mask network host address", ["subnet mask network host"])
    assert not too_close("subnet mask", ["subnet mask network host"])


def test_no_earlier_prompts():
    assert not too_close("What does ARP resolve?", [])
