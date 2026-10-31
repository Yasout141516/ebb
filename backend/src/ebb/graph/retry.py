"""The one citation retry: question batches, grades, lessons (quiz and learn phase) and
re-checks all fix their citations through fix_citations."""

from collections.abc import Callable, Sequence

from ebb.citations import MAX_RETRIES, verify
from ebb.domain import Citation, Claim, Source
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


def with_citations[T](
    generated: Generated[T],
    claims_of: Callable[[T], Sequence[Claim]],
    sources: Sequence[Source],
    fix: Callable[[Generated[T], tuple[str, ...]], Generated[T]],
) -> tuple[Generated[T], tuple[Citation, ...]]:
    """`generated` once `fix` has had its tries at any invalid claim (see fix_citations),
    with the citations that hold. Missing claims aren't retried, and nothing counts as
    cited when there are no sources."""
    if not sources:
        return generated, ()
    fixed = fix_citations(generated, lambda value: verify(claims_of(value), sources)[1], fix)
    return fixed, verify(claims_of(fixed.value), sources)[0]
