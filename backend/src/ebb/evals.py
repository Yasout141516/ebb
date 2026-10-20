"""The grading eval: labelled short-answer cases and the report that scores the grader.

Cases are written once by a model other than the grader (the label is the grade the
writer was asked to aim for, not a person's verdict), then frozen in a JSON Lines file.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from ebb.chunking import chunk_notes
from ebb.citations import note_sources, source_from_dict, source_to_dict, verify
from ebb.domain import Chunk, Grade, GradeValue, Question, Source
from ebb.llm.tutor import AllProvidersExhausted, Tutor
from ebb.notes import read_notes

PACE_SECONDS = 2.0
TARGET_AGREEMENT = 0.9
GROUNDED_CASES = 10

EVAL_TOPICS = (
    "Subnetting and usable host counts",
    "CIDR notation and address aggregation",
    "Private and reserved IPv4 address ranges",
    "IPv6 addressing and address types",
    "ARP and how a host finds a MAC address",
    "The default gateway and local versus remote delivery",
    "DNS resolution from resolver to authoritative server",
    "DNS record types",
    "DNS caching and TTL",
    "DHCP and the lease process",
    "NAT and port address translation",
    "Port forwarding and static NAT",
    "The OSI model layers",
    "The TCP/IP model compared with OSI",
    "TCP three-way handshake",
    "TCP connection teardown",
    "TCP flow control and the window size",
    "TCP congestion control",
    "TCP versus UDP",
    "Port numbers and sockets",
    "Ethernet frames and MAC addresses",
    "Switches and MAC address tables",
    "Hubs, switches and routers compared",
    "VLANs and trunking",
    "Spanning Tree Protocol and loop prevention",
    "Routing tables and longest prefix match",
    "Static versus dynamic routing",
    "Distance vector versus link state routing",
    "OSPF basics",
    "BGP and autonomous systems",
    "ICMP, ping and traceroute",
    "TTL in IP packets",
    "IP fragmentation and the MTU",
    "HTTP request and response basics",
    "HTTPS and TLS handshake basics",
    "Firewalls and access control lists",
    "VPNs and tunnelling",
    "Wi-Fi basics: SSIDs, channels and WPA",
    "Latency, bandwidth and throughput",
    "Load balancers and reverse proxies",
)

_TARGETS = (GradeValue.CORRECT, GradeValue.PARTIAL, GradeValue.INCORRECT)
_RANK = {GradeValue.INCORRECT: 0, GradeValue.PARTIAL: 1, GradeValue.CORRECT: 2}
_ORDER = ("correct", "partial", "incorrect")
_CHUNK_NAMESPACE = uuid5(NAMESPACE_URL, "ebb:eval-notes")


@dataclass(frozen=True)
class EvalCase:
    id: str
    topic: str
    question: str
    reference: str
    answer: str
    label: GradeValue
    reason: str
    sources: tuple[Source, ...]
    writer_model: str


@dataclass(frozen=True)
class EvalScore:
    total: int
    agreed: int
    agreement: float
    confusion: dict[str, dict[str, int]]
    too_harsh: int
    too_lenient: int
    disagreements: list[dict]
    grounded_total: int
    grounded_cited: int


def _note_chunks(notes_path: Path) -> list[Chunk]:
    """The sample notes as chunks with ids that are the same on every build."""
    notes = read_notes(notes_path)
    return [
        Chunk(
            uuid5(_CHUNK_NAMESPACE, f"{notes.filename}:{draft.ordinal}"),
            notes.filename,
            draft.content,
            draft.heading,
            draft.page_start,
            draft.page_offsets,
        )
        for draft in chunk_notes(notes)
    ]


def build_cases(
    writer: Tutor,
    topics: Sequence[str],
    notes_path: Path,
    grounded: int = GROUNDED_CASES,
    sleep: Callable[[float], None] = time.sleep,
) -> list[EvalCase]:
    """One case per topic, then `grounded` cases on passages of the notes.

    Targets cycle correct, partial, incorrect. A case the writer can't produce is
    skipped, so the result can be shorter than `len(topics) + grounded`.
    """
    chunks = _note_chunks(notes_path) if grounded else []
    jobs: list[tuple[str, Chunk | None]] = [(topic, None) for topic in topics]
    jobs += [
        (chunks[n % len(chunks)].heading or "the notes", chunks[n % len(chunks)])
        for n in range(grounded if chunks else 0)
    ]
    cases = []
    for number, (topic, chunk) in enumerate(jobs, start=1):
        target = _TARGETS[(number - 1) % len(_TARGETS)]
        try:
            made = writer.write_eval_case(topic, target, chunk.content if chunk else None)
        except AllProvidersExhausted:
            sleep(PACE_SECONDS)
            continue
        draft = made.value
        sources = tuple(note_sources([chunk])) if chunk else ()
        cases.append(
            EvalCase(
                f"case-{number:03d}",
                topic,
                draft.question,
                draft.reference,
                draft.answer,
                target,
                draft.reason,
                sources,
                made.model,
            )
        )
        sleep(PACE_SECONDS)
    return cases


def _to_row(case: EvalCase) -> dict:
    return {
        "id": case.id,
        "topic": case.topic,
        "question": case.question,
        "reference": case.reference,
        "answer": case.answer,
        "label": case.label.value,
        "reason": case.reason,
        "sources": [source_to_dict(s) for s in case.sources],
        "writer_model": case.writer_model,
    }


def _from_row(row: dict) -> EvalCase:
    return EvalCase(
        row["id"],
        row["topic"],
        row["question"],
        row["reference"],
        row["answer"],
        GradeValue(row["label"]),
        row["reason"],
        tuple(source_from_dict(s) for s in row["sources"]),
        row["writer_model"],
    )


def save_cases(cases: Sequence[EvalCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_to_row(c), ensure_ascii=False) for c in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_cases(path: Path) -> list[EvalCase]:
    text = path.read_text(encoding="utf-8")
    return [_from_row(json.loads(line)) for line in text.splitlines() if line.strip()]


def _question(case: EvalCase) -> Question:
    id_ = uuid5(_CHUNK_NAMESPACE, case.id)
    return Question(
        id=id_,
        concept_id=UUID(int=0),
        type="short",
        difficulty=1,
        prompt=case.question,
        reference_answer=case.reference,
        source="notes" if case.sources else "general",
    )


def run_cases(
    grader: Tutor,
    cases: Sequence[EvalCase],
    sleep: Callable[[float], None] = time.sleep,
) -> list[Grade | None]:
    """Grade every case; None where every attempt failed."""
    grades: list[Grade | None] = []
    for case in cases:
        try:
            grades.append(grader.grade(_question(case), case.answer, case.sources).value)
        except AllProvidersExhausted:
            grades.append(None)
        sleep(PACE_SECONDS)
    return grades


def score(cases: Sequence[EvalCase], grades: Sequence[Grade | None]) -> EvalScore:
    confusion = {label: dict.fromkeys(_ORDER, 0) for label in _ORDER}
    agreed = harsh = lenient = grounded_total = grounded_cited = 0
    disagreements: list[dict] = []
    for case, grade in zip(cases, grades, strict=True):
        if grade is not None:
            confusion[case.label.value][grade.value.value] += 1
        if case.sources:
            grounded_total += 1
            if grade is not None and verify(grade.claims, case.sources)[0]:
                grounded_cited += 1
        if grade is not None and grade.value is case.label:
            agreed += 1
            continue
        if grade is not None:
            if _RANK[grade.value] < _RANK[case.label]:
                harsh += 1
            else:
                lenient += 1
        disagreements.append(
            {
                "id": case.id,
                "question": case.question,
                "answer": case.answer,
                "label": case.label.value,
                "grade": grade.value.value if grade else "error",
                "explanation": grade.explanation if grade else "Grading failed.",
            }
        )
    total = len(cases)
    return EvalScore(
        total,
        agreed,
        agreed / total if total else 0.0,
        confusion,
        harsh,
        lenient,
        disagreements,
        grounded_total,
        grounded_cited,
    )


def agreement_line(result: EvalScore) -> str:
    target = round(TARGET_AGREEMENT * 100)
    return (
        f"Agreement: {result.agreed}/{result.total} "
        f"({result.agreement * 100:.1f}%) — target {target}%"
    )


def report_markdown(
    result: EvalScore,
    model: str,
    day: date,
    disputes: dict[str, int] | None,
    writer_model: str = "the writer model",
) -> str:
    lines = [
        f"# Grading eval: {model}, {day.isoformat()}",
        "",
        agreement_line(result),
        "",
        "| label \\ grade | correct | partial | incorrect |",
        "| --- | --- | --- | --- |",
    ]
    for label in _ORDER:
        row = result.confusion[label]
        lines.append(f"| {label} | " + " | ".join(str(row[g]) for g in _ORDER) + " |")
    lines += [
        "",
        f"Too harsh: {result.too_harsh} · Too lenient: {result.too_lenient}",
        "",
        f"Grounded cases with a valid citation: {result.grounded_cited}/{result.grounded_total}",
        "",
    ]
    if disputes is None:
        lines.append("Disputes: database not reachable")
    else:
        lines.append(
            f"Disputes: {disputes['raised']} raised · {disputes['changed']} changed · "
            f"{disputes['voided']} voided · {disputes['upheld']} upheld"
        )
    lines += ["", "## Disagreements", ""]
    if not result.disagreements:
        lines.append("None.")
    for d in result.disagreements:
        lines += [
            f"- **{d['question']}**",
            f"  - Answer: {d['answer']}",
            f"  - Label: {d['label']} · Grade: {d['grade']}",
            f"  - Explanation: {d['explanation']}",
        ]
    lines += [
        "",
        f"Labels come from {writer_model}, the writer model that generated each case: "
        "a model, not a person. A disagreement can be a wrong label as well as a wrong grade.",
        "",
    ]
    return "\n".join(lines)
