"""Defaults for the graph state keys added after milestone 1."""

from ebb.graph.state import STATE_DEFAULTS, fill_defaults


def test_fill_defaults_fills_only_missing_keys():
    values = {"web_failed": True, "notices": ["x"]}

    filled = fill_defaults(values)

    assert filled["sources"] == []
    assert filled["web_searched"] == []
    assert filled["earlier_prompts"] is None
    assert filled["citation_failing"] is None
    assert filled["web_failed"] is True  # present keys are untouched
    assert filled["notices"] == ["x"]
    assert values == {"web_failed": True, "notices": ["x"]}  # the input isn't mutated


def test_filled_defaults_are_fresh_lists():
    first, second = fill_defaults({}), fill_defaults({})

    first["sources"].append({})

    assert second["sources"] == []
    assert STATE_DEFAULTS["sources"] == []
