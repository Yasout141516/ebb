"""The daily AI-call cap: CappedTutor in front of any tutor."""

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ebb.domain import Concept
from ebb.llm.quota import CappedTutor, UserQuotaExceeded
from ebb.llm.tutor import TutorUnavailable
from tests.fakes import FakeTutor, MemoryStore
from tests.test_graph import QUOTE, notes_subject, run

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
USER = uuid4()
TODAY = (USER, NOW.date())


def capped(inner, store, limit):
    return CappedTutor(inner, store, USER, limit, ZoneInfo("UTC"), lambda: NOW)


def test_the_quota_error_is_a_tutor_unavailable_with_the_daily_notice():
    error = UserQuotaExceeded()
    assert isinstance(error, TutorUnavailable)
    assert error.notice == "Daily limit reached — your progress is saved. It resets at midnight."


def test_capped_tutor_refuses_without_calling_inner():
    inner, store = FakeTutor(), MemoryStore()
    tutor = capped(inner, store, 1)

    tutor.extend_concepts("Networks", [])
    with pytest.raises(UserQuotaExceeded):
        tutor.extend_concepts("Networks", [])

    assert len(inner.extend_calls) == 1
    assert store.ai_calls == {TODAY: 1}


def test_capped_tutor_with_limit_zero_never_calls_inner_or_writes():
    inner, store = FakeTutor(), MemoryStore()

    with pytest.raises(UserQuotaExceeded):
        capped(inner, store, 0).concepts_from_notes("Networks", [], "digest")

    assert inner.concepts_calls == []
    assert store.ai_calls == {}


def test_capped_tutor_counts_learn_calls():
    inner, store = FakeTutor(), MemoryStore()
    tutor = capped(inner, store, 2)
    concept = Concept(uuid4(), uuid4(), "DNS", "Names to addresses.")
    pieces: list[str] = []

    tutor.write_lesson(concept, "Networks", [], on_text=pieces.append)
    tutor.answer(concept, "# DNS", [], "Why?", [], on_text=pieces.append)
    with pytest.raises(UserQuotaExceeded):
        tutor.answer(concept, "# DNS", [], "Why again?", [])

    assert (len(inner.lesson_calls), len(inner.answer_calls)) == (1, 1)
    assert len(pieces) == 6  # each streamed through, three pieces apiece
    assert store.ai_calls == {TODAY: 2}


def test_citation_retries_count_as_calls():
    store, inner = MemoryStore(), FakeTutor(cite=[[("N9", "x")], [("N1", QUOTE)]])
    subject_id = notes_subject(store)

    run(capped(inner, store, 50), store, subject_id, lambda p: {"quit": True})

    assert len(inner.generate_calls) == 2  # the first batch failed its citations
    assert store.ai_calls == {TODAY: 2}
