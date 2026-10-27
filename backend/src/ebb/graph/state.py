"""Graph state and its (de)serialisation.

State holds only JSON-style values (str, int, list, dict). LangGraph checkpointers
warn on — and will soon block — custom classes, and turn tuples into lists, so
domain objects are converted at the node boundary with the helpers below.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal, TypedDict
from uuid import UUID

from ebb.citations import from_dict as citation_from_dict
from ebb.citations import to_dict as citation_to_dict
from ebb.domain import Citation, Claim, Concept, ConceptResult, Grade, GradeValue, Option, Question
from ebb.routing import candidate_run


class QuizState(TypedDict, total=False):
    # set by the caller
    user_id: str
    subject_id: str
    thread_id: str
    # session
    session_id: str
    studied_concept_ids: list[str]
    results: list[dict[str, Any]]
    quit: bool
    caught_up: bool  # everything learned and the tutor had nothing more to add
    fatal: str | None
    notices: list[str]
    has_notes: bool  # the subject has documents
    web_searched: list[str]  # ids of the concepts searched for on the web
    web_failed: bool  # a web search failed, so no more are tried
    web_notice_shown: bool
    # current concept
    concept: dict[str, Any] | None
    batch: list[dict[str, Any]]
    asked_prompts: list[str]
    asked_on_concept: int
    correct_streak: int
    generation_calls_on_concept: int
    sources: list[dict[str, Any]]  # the concept's retrieved note chunks, as Source dicts
    earlier_prompts: list[str]  # earlier sessions' answered prompts on the concept
    # The candidate questions being written: {mode, candidates, replacing}. `mode` is "web"
    # while replacements for repeats are written from the web; `candidates` wait for
    # verify_citations; `replacing` counts the repeats dropped from the last notes batch.
    run: dict[str, Any]
    web_sources: list[dict[str, Any]]  # the concept's web pages, as Source dicts
    # current question
    question: dict[str, Any] | None
    difficulty: int | None
    # The question ask_question must show next: after a dispute the interrupted one, after a
    # failed grade the failed one (which is also back at the front of `batch`).
    current_question_id: str | None
    attempt_id: str | None
    answer: str | None
    grade: dict[str, Any] | None
    model_used: str | None
    lesson: dict[str, Any] | None  # {text, citations}
    # disputes
    # The last short answer graded by the LLM, kept until the next answer so it can be
    # disputed: {attempt_id, concept_id, question, answer, grade, sources, disputed}.
    last_graded: dict[str, Any] | None
    dispute: dict[str, Any] | None  # {id, reason, explanation} waiting for regrade
    dispute_result: dict[str, Any] | None  # shown above the re-shown question
    # Keys of sessions saved before milestone 4, kept so their checkpoints still load;
    # fill_defaults moves them to current_question_id and lesson, and nothing writes them.
    pending_question_id: str | None
    retry_question: bool
    lesson_citations: list[dict[str, Any]]
    # Keys of sessions saved before `run`. LangGraph drops keys on load that aren't declared
    # here; fill_defaults moves them into `run`, and nothing writes them.
    generation_mode: Literal["notes", "web"]
    candidates: list[dict[str, Any]]
    replacements_needed: int
    # Keys of sessions saved while verify_citations sent its candidates back to
    # generate_questions for a citation fix; it fixes them itself now. fill_defaults drops
    # them, and nothing writes them.
    citation_retries: int
    citation_failures: list[str]
    citation_failing: list[int]
    # Keys of sessions saved before the session summary was read from the store. fill_defaults
    # drops them, and nothing writes them.
    start: dict[str, Any]
    tally: dict[str, int]


# Keys added after milestone 1, with what a session saved before them gets. `None` for
# earlier_prompts means "ask the store".
STATE_DEFAULTS: dict[str, Any] = {
    "has_notes": False,
    "web_searched": [],
    "web_failed": False,
    "web_notice_shown": False,
    "sources": [],
    "earlier_prompts": None,
    "web_sources": [],
    "last_graded": None,
    "dispute_result": None,
    "current_question_id": None,
}


def fill_defaults(values: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of `values` with every absent key of STATE_DEFAULTS filled in, and the keys
    older sessions saved moved to the ones that replaced them."""
    filled = {**deepcopy(STATE_DEFAULTS), **values}
    # pending_question_id and retry_question became current_question_id. Once a node has
    # written that key the old ones are stale, since nothing clears them.
    pending = filled.pop("pending_question_id", None)
    retry = filled.pop("retry_question", False)
    if "current_question_id" not in values:
        if pending:
            filled["current_question_id"] = pending
        elif retry and filled.get("batch"):
            filled["current_question_id"] = filled["batch"][0]["id"]
    # generation_mode, candidates and replacements_needed became `run`. Once a node has
    # written `run` the old ones are stale, since nothing clears them.
    filled["run"] = candidate_run(values)
    for old in ("generation_mode", "candidates", "replacements_needed"):
        filled.pop(old, None)
    # A session saved mid-retry gets a fresh batch: generate_questions no longer rewrites,
    # so the failures it was sent back with are of no use.
    for old in ("citation_retries", "citation_failures", "citation_failing"):
        filled.pop(old, None)
    # The summary is read from the store at the end, so a saved tally and start are of no use.
    for old in ("start", "tally"):
        filled.pop(old, None)
    # A lesson was a string, with its citations beside it.
    citations = filled.pop("lesson_citations", None)
    if isinstance(filled.get("lesson"), str):
        filled["lesson"] = {"text": filled["lesson"], "citations": citations or []}
    return filled


def fill_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a waiting question's payload (or a session's final turn) in the current
    shape: milestone 3 saved the lesson as a string with its citations beside it, and
    citations without name, heading or title."""
    filled = deepcopy(dict(payload))
    citations = filled.pop("lesson_citations", None)
    if isinstance(filled.get("lesson"), str):
        filled["lesson"] = {"text": filled["lesson"], "citations": citations or []}
    for key in ("feedback", "grade", "lesson", "dispute_result"):
        part = filled.get(key)
        if part and "citations" in part:
            part["citations"] = [citation_to_dict(c) for c in _citations(part["citations"])]
    return filled


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def concept_to_dict(c: Concept) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "subject_id": str(c.subject_id),
        "name": c.name,
        "description": c.description,
        "stage": c.stage,
        "next_review_at": _dt(c.next_review_at),
        "last_reviewed_at": _dt(c.last_reviewed_at),
        "review_count": c.review_count,
    }


def result_to_dict(r: ConceptResult) -> dict[str, Any]:
    """A summary row as `ui.render_summary` reads it."""
    return {
        "concept": r.concept,
        "concept_id": str(r.concept_id),
        "tally": {"correct": r.correct, "partial": r.partial, "incorrect": r.incorrect},
        "stage_before": r.stage_before,
        "stage_after": r.stage_after,
        "tide_before": r.tide_before,
        "tide_after": r.tide_after,
        "next_review_at": _dt(r.next_review_at),
    }


def concept_from_dict(d: dict[str, Any]) -> Concept:
    return Concept(
        id=UUID(d["id"]),
        subject_id=UUID(d["subject_id"]),
        name=d["name"],
        description=d["description"],
        stage=d["stage"],
        next_review_at=_parse_dt(d["next_review_at"]),
        last_reviewed_at=_parse_dt(d["last_reviewed_at"]),
        review_count=d["review_count"],
    )


def _claims(items: list[dict[str, Any]]) -> tuple[Claim, ...]:
    return tuple(Claim(c["source"], c["quote"]) for c in items)


def _citations(items: list[dict[str, Any]]) -> tuple[Citation, ...]:
    return tuple(citation_from_dict(c) for c in items)


# Dicts saved by milestone 2 lack source, claims and citations, hence the defaults below.


def question_to_dict(q: Question) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "concept_id": str(q.concept_id),
        "type": q.type,
        "difficulty": q.difficulty,
        "prompt": q.prompt,
        "reference_answer": q.reference_answer,
        "options": [asdict(o) for o in q.options],
        "source": q.source,
        "claims": [asdict(c) for c in q.claims],
        "citations": [citation_to_dict(c) for c in q.citations],
    }


def question_from_dict(d: dict[str, Any]) -> Question:
    return Question(
        id=UUID(d["id"]),
        concept_id=UUID(d["concept_id"]),
        type=d["type"],
        difficulty=d["difficulty"],
        prompt=d["prompt"],
        reference_answer=d["reference_answer"],
        options=tuple(Option(o["text"], o["is_correct"], o["why"]) for o in d["options"]),
        source=d.get("source", "general"),
        claims=_claims(d.get("claims", [])),
        citations=_citations(d.get("citations", [])),
    )


def grade_to_dict(g: Grade, general_knowledge: bool = False) -> dict[str, Any]:
    """`general_knowledge`: the subject has notes, yet nothing from them backs this grade."""
    return {
        "value": g.value.value,
        "explanation": g.explanation,
        "misconception": g.misconception,
        "claims": [asdict(c) for c in g.claims],
        "citations": [citation_to_dict(c) for c in g.citations],
        "general_knowledge": general_knowledge,
    }


def grade_from_dict(d: dict[str, Any]) -> Grade:
    return Grade(
        GradeValue(d["value"]),
        d["explanation"],
        d["misconception"],
        claims=_claims(d.get("claims", [])),
        citations=_citations(d.get("citations", [])),
    )
