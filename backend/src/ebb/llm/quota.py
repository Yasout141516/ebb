"""The daily cap on AI calls, shared by every caller of the tutor."""

from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from ebb.domain import (
    Concept,
    DisputeReason,
    Grade,
    GradeValue,
    LearnText,
    Lesson,
    Question,
    Regrade,
    Source,
)
from ebb.llm.schemas import ConceptDraft, EvalCaseDraft, NoteConcept
from ebb.llm.tutor import Generated, Tutor, TutorUnavailable
from ebb.store import Store


class UserQuotaExceeded(TutorUnavailable):
    """The user has made today's allowance of AI calls."""

    notice = "Daily limit reached — your progress is saved. It resets at midnight."


class CappedTutor:
    """A Tutor that takes one call from the user's daily allowance before every method,
    and raises UserQuotaExceeded, without asking `inner`, once it is spent. Each retry the
    graph makes (citation retries included) is a call of its own."""

    def __init__(
        self,
        inner: Tutor,
        store: Store,
        user_id: UUID,
        limit: int,
        tz: ZoneInfo,
        now: Callable[[], datetime],
    ) -> None:
        self.inner = inner
        self.store = store
        self.user_id = user_id
        self.limit = limit
        self.tz = tz
        self.now = now

    def _take(self) -> None:
        day = self.now().astimezone(self.tz).date()
        if not self.store.take_ai_call(self.user_id, day, self.limit):
            raise UserQuotaExceeded()

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        self._take()
        return self.inner.extract_concepts(topic)

    def extend_concepts(
        self, subject: str, existing: Sequence[Concept]
    ) -> Generated[list[ConceptDraft]]:
        self._take()
        return self.inner.extend_concepts(subject, existing)

    def generate_questions(
        self,
        concept: Concept,
        avoid: list[str],
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        web_count: int | None = None,
        rewrite: Sequence[Question] = (),
    ) -> Generated[list[Question]]:
        self._take()
        return self.inner.generate_questions(concept, avoid, sources, failures, web_count, rewrite)

    def grade(
        self,
        question: Question,
        answer: str,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Grade | None = None,
    ) -> Generated[Grade]:
        self._take()
        return self.inner.grade(question, answer, sources, failures, previous)

    def teach(
        self,
        question: Question,
        answer: str,
        grade: Grade,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Lesson | None = None,
    ) -> Generated[Lesson]:
        self._take()
        return self.inner.teach(question, answer, grade, sources, failures, previous)

    def write_lesson(
        self,
        concept: Concept,
        subject: str,
        sources: Sequence[Source],
        on_text: Callable[[str], None] | None = None,
        failures: Sequence[str] = (),
        previous: LearnText | None = None,
    ) -> Generated[LearnText]:
        self._take()
        return self.inner.write_lesson(concept, subject, sources, on_text, failures, previous)

    def answer(
        self,
        concept: Concept,
        lesson: str,
        history: Sequence[tuple[str, str]],
        question: str,
        sources: Sequence[Source],
        on_text: Callable[[str], None] | None = None,
        failures: Sequence[str] = (),
        previous: LearnText | None = None,
    ) -> Generated[LearnText]:
        self._take()
        return self.inner.answer(
            concept, lesson, history, question, sources, on_text, failures, previous
        )

    def concepts_from_notes(
        self, subject: str, existing: Sequence[Concept], digest: str
    ) -> Generated[list[NoteConcept]]:
        self._take()
        return self.inner.concepts_from_notes(subject, existing, digest)

    def regrade(
        self,
        question: Question,
        answer: str,
        grade: Grade,
        reason: DisputeReason,
        explanation: str,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Grade | None = None,
    ) -> Generated[Regrade]:
        self._take()
        return self.inner.regrade(
            question, answer, grade, reason, explanation, sources, failures, previous
        )

    def write_eval_case(
        self, topic: str, target: GradeValue, notes: str | None
    ) -> Generated[EvalCaseDraft]:
        self._take()
        return self.inner.write_eval_case(topic, target, notes)
