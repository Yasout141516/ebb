"""The one citation retry: question batches, grades, lessons and re-checks all fix their
citations through fix_citations."""

from collections.abc import Callable

from ebb.citations import MAX_RETRIES
from ebb.llm.tutor import Generated, TutorUnavailable


def fix_citations[T](
    generated: Generated[T],
    check: Callable[[T], tuple[str, ...]],
    fix: Callable[[Generated[T], tuple[str, ...]], Generated[T]],
) -> Generated[T]:
    """`generated`, or the fix of it `check` finds nothing wrong with. While `check` finds
    failures, `fix` gets the last result and them, at most MAX_RETRIES times; a fix that
    fails keeps the last result."""
    failures = check(generated.value)
    for _ in range(MAX_RETRIES):
        if not failures:
            break
        try:
            generated = fix(generated, failures)
        except TutorUnavailable:
            break
        failures = check(generated.value)
    return generated
