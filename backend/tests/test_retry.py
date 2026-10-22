"""The one citation-retry loop shared by question batches, grades, lessons and re-checks."""

import pytest

from ebb.citations import MAX_RETRIES
from ebb.graph.retry import fix_citations
from ebb.llm.tutor import AllProvidersExhausted, Generated, TutorRefused


def failing_while_below(clean: int):
    """A check that fails every value below `clean`."""
    return lambda value: () if value >= clean else (f"{value} is too small",)


def test_fix_citations_stops_when_clean():
    fixes = []

    def fix(last, failures):
        fixes.append((last.value, failures))
        return Generated(last.value + 1, "fixer")

    fixed = fix_citations(Generated(0, "first"), failing_while_below(1), fix)
    clean = fix_citations(Generated(5, "first"), failing_while_below(1), fix)

    assert fixes == [(0, ("0 is too small",))]  # one fix, told why; none for the clean value
    assert fixed == Generated(1, "fixer")
    assert clean == Generated(5, "first")


def test_fix_citations_tries_at_most_max_retries():
    fixes = []

    def fix(last, failures):
        fixes.append(last.value)
        return Generated(last.value + 1, "fixer")

    fixed = fix_citations(Generated(0, "first"), failing_while_below(100), fix)

    assert MAX_RETRIES == 2
    assert fixes == [0, 1]  # each fix gets the last value
    assert fixed.value == 2  # the last fix is kept even though it still fails


@pytest.mark.parametrize("error", [AllProvidersExhausted("busy"), TutorRefused()])
def test_fix_citations_keeps_last_value_when_fix_is_unavailable(error):
    calls = []

    def fix(last, failures):
        calls.append(last.value)
        if len(calls) == 2:
            raise error
        return Generated(last.value + 1, "fixer", fell_back=True)

    fixed = fix_citations(Generated(0, "first"), failing_while_below(100), fix)

    assert calls == [0, 1]
    assert fixed == Generated(1, "fixer", fell_back=True)
