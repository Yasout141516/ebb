"""Creating subjects and adding notes: the flows shared by the CLI and the API.

Failures propagate (TutorUnavailable, TutorRefused, ConfigError, SubjectExists,
DocumentRefused); each caller maps them to its own output.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from ebb.chunking import digest
from ebb.domain import Concept, NewDocument, Subject
from ebb.store import MAX_DOCUMENTS, DocumentRefused, Store, SubjectExists, merge_note_concepts

if TYPE_CHECKING:
    from ebb.llm.tutor import Tutor


@dataclass(frozen=True)
class NotesReport:
    matched: list[str]  # names of concepts the subject already had
    fresh: list[tuple[str, str]]  # new (name, description)


# A refusal while finding the concepts in uploaded notes.
NOTES_REFUSED_NOTICE = "The tutor won't help with these notes."


def too_many_copy(name: str) -> str:
    return f"{name} can have at most {MAX_DOCUMENTS} files."


def full_copy(name: str) -> str:
    return f"{name} already has {MAX_DOCUMENTS} of {MAX_DOCUMENTS} files."


def duplicate_copy(name: str, filename: str) -> str:
    return f"{filename} is already in {name}."


def room_copy(error: DocumentRefused, name: str, filename: str) -> str:
    """What the learner is told when `ensure_room` or the store refuses a file."""
    return full_copy(name) if error.reason == "full" else duplicate_copy(name, filename)


def find_subject(subjects: Sequence[Subject], name: str) -> Subject | None:
    """The subject called `name`, if any. Names are compared stripped and with lower(), as the
    unique index on lower(name) compares them."""
    wanted = name.strip().lower()
    return next((s for s in subjects if s.name.lower() == wanted), None)


def ensure_name_free(store: Store, user_id: UUID, name: str) -> str:
    """The stripped name; raises SubjectExists (naming the existing subject) if it is taken."""
    name = name.strip()
    if existing := find_subject(store.list_subjects(user_id), name):
        raise SubjectExists(existing.name)
    return name


def ensure_room(store: Store, user_id: UUID, subject: Subject, document: NewDocument) -> None:
    """Raises DocumentRefused if the subject is full or already has this file."""
    if store.count_documents(user_id, subject.id) >= MAX_DOCUMENTS:
        raise DocumentRefused("full")
    if store.has_document(user_id, subject.id, document.sha256):
        raise DocumentRefused("duplicate")


def subject_from_topic(
    tutor: Tutor, store: Store, user_id: UUID, topic: str
) -> tuple[UUID, list[tuple[str, str]]]:
    """Create a subject from a topic alone. Raises SubjectExists, before any AI call when
    the name is already taken."""
    topic = ensure_name_free(store, user_id, topic)
    generated = tutor.extract_concepts(topic)
    concepts = [(c.name, c.description) for c in generated.value]
    return store.create_subject(user_id, topic, concepts), concepts


def find_note_concepts(
    tutor: Tutor, subject: str, known: Sequence[tuple[str, str]], document: NewDocument
) -> NotesReport:
    """Ask for the concepts in one file, split into those in `known` and new ones."""
    # Placeholders: the prompt only uses names and descriptions, so these ids are never stored.
    placeholder = uuid4()
    existing = [Concept(uuid4(), placeholder, name, text) for name, text in known]
    generated = tutor.concepts_from_notes(subject, existing, digest(document.chunks))
    found = [(c.name, c.description, c.same_as) for c in generated.value]
    matched, fresh = merge_note_concepts([name for name, _ in known], found)
    return NotesReport(matched, fresh)


def add_notes(
    tutor: Tutor, store: Store, user_id: UUID, subject: Subject, document: NewDocument
) -> NotesReport:
    """Find the concepts in a file and store it with them. Raises DocumentRefused when the
    subject is full or already has the file."""
    ensure_room(store, user_id, subject, document)
    known = [(c.name, c.description) for c in store.list_concepts(user_id, subject.id)]
    report = find_note_concepts(tutor, subject.name, known, document)
    store.add_document(user_id, subject.id, document, report.fresh)
    return report
