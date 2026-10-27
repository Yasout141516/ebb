"""Defaults for the graph state keys added after milestone 1."""

from ebb.graph.state import STATE_DEFAULTS, fill_defaults, fill_payload


def test_fill_defaults_fills_only_missing_keys():
    values = {"web_failed": True, "notices": ["x"]}

    filled = fill_defaults(values)

    assert filled["sources"] == []
    assert filled["web_searched"] == []
    assert filled["earlier_prompts"] is None
    assert filled["web_failed"] is True  # present keys are untouched
    assert filled["notices"] == ["x"]
    assert values == {"web_failed": True, "notices": ["x"]}  # the input isn't mutated


def test_fill_defaults_drops_the_old_citation_retry_keys():
    old = {"citation_retries": 1, "citation_failures": ["question 1 cites nothing"]}

    filled = fill_defaults({**old, "citation_failing": [0]})

    assert not {"citation_retries", "citation_failures", "citation_failing"} & set(filled)


def test_filled_defaults_are_fresh_lists():
    first, second = fill_defaults({}), fill_defaults({})

    first["sources"].append({})

    assert second["sources"] == []
    assert STATE_DEFAULTS["sources"] == []


OLD_CITATION = {
    "kind": "web",
    "ref": "https://a.org/x",
    "label": "a.org · X",
    "page": None,
    "quote": "Q.",
}
CITATION = OLD_CITATION | {"name": "a.org", "heading": None, "title": "X"}


def test_fill_payload_gives_a_milestone_3_question_the_current_shape():
    old = {
        "concept": "DNS",
        "feedback": {"value": "incorrect", "explanation": "No.", "citations": [OLD_CITATION]},
        "lesson": "An old lesson.",
        "lesson_citations": [OLD_CITATION],
        "dispute_result": {
            "outcome": "upheld",
            "original_grade": "incorrect",
            "new_grade": None,
            "explanation": "It stands.",
            "citations": [OLD_CITATION],
            "concept": "DNS",
            "stage": 1,
        },
    }

    filled = fill_payload(old)

    assert filled["lesson"] == {"text": "An old lesson.", "citations": [CITATION]}
    assert "lesson_citations" not in filled
    assert filled["dispute_result"]["citations"] == [CITATION]
    assert filled["feedback"]["citations"] == [CITATION]
    assert old["lesson"] == "An old lesson."  # the input isn't mutated
    assert old["feedback"]["citations"] == [OLD_CITATION]


def test_fill_payload_gives_a_final_turn_saved_by_milestone_3_the_current_shape():
    final = {"grade": {"value": "correct", "citations": [OLD_CITATION]}, "lesson": None}

    assert fill_payload(final)["grade"]["citations"] == [CITATION]


def test_fill_payload_keeps_a_current_question_as_it_is():
    current = {
        "feedback": {"value": "correct", "explanation": "Yes.", "citations": [CITATION]},
        "lesson": None,
        "dispute_result": {"outcome": "refused"},
    }

    assert fill_payload(current) == current
