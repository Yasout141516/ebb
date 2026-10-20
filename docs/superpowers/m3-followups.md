# Milestone 3 — follow-ups

Items found while building and reviewing milestone 3 that were deliberately left for
later. Earlier items: [m1-followups.md](m1-followups.md), [m2-followups.md](m2-followups.md).

## Safety (before milestone 4/6, when other people use Ebb)

- **A Gemini safety block is treated as an ordinary failure** and retried on the weaker Groq
  backup. Detect a block, show a clear message, and never fall back.
- **No content check of our own** on topics or uploaded notes; we rely on the providers' filters.
- **`ebb new` topics and concept names go into prompts unfenced** (and `concepts_from_notes`
  interpolates them un-neutralised, so a newline could break the listing). Answers and notes are
  fenced; fence these too, and the LLM-generated question text.
- **Per-user rate limits** for model and search calls.

## Correctness to fix

- **Answer counts over `attempts` must exclude voided ones.** Any count or "answered prompts" query
  that reads `attempts` has to skip answers whose dispute was voided.
- **"Latest attempt" uses `graded_at`, not insertion order.** Two simultaneous sessions on one
  concept could void the wrong snapshot.
- **Malformed PDFs** can raise errors other than `PyPdfError` (`ValueError`, `KeyError`,
  `zlib.error`), which show a traceback instead of "Can't open".
- **A failed dispute on resume**: a store refusal marks the answer disputed so `d` is not offered
  again; a malformed dispute id raises outside the pre-check.
- Two terminals racing `ebb new` on one name get the database error, not the friendly message.

## Tune against real notes (Task 15)

- **`MIN_RANK` (0.05)**: check retrieval on a real lecture PDF. Too high turns covered concepts into
  "General knowledge"; too low lets loosely related passages through.
- **The repeat threshold (0.6) for short questions**: two short questions that share most words
  count as repeats and cost a web search. Consider a minimum word count before the overlap rule.
- **The `un-` prefix in repeats** is stripped, so usable and unusable collide; the stemmer also
  misses -es/-ies plurals (only misses a repeat).
- Repeat detection compares against the 40 most recent answered prompts; raise the cap if older
  repeats slip through.

## Behaviour worth revisiting

- **`extend_concepts` near-duplicates are still open** (see m2-followups): rewordings slip past the
  exact-name check.
- Web replacements are not checked against sibling notes questions in the same batch.
- An empty first search marks the concept searched, so it gets no web questions that session.
- If every provider fails during a citation retry, that batch becomes general-knowledge questions.
- Wrapped citation quotes lose their indent; the invalid-input hint omits `d`; "1 concepts" and
  "1 passages" plurals; web labels with an empty host or title fall back awkwardly.
- `_today()` is called twice in `eval run` (heading vs file name across midnight), and the report
  names the configured primary model rather than the grader's.

## Schema and tests (low priority)

- `grade_disputes` has no check tying `new_grade` null to outcome voided; no `subject_id` index
  on `document_chunks`.
- Concurrency and `create_subject_with_notes` transaction tests are smoke tests, not proof.
- Missing tests: both extra `new --notes` refusals, lesson citation rendering, a mixed
  notes/web batch, teaching a web question, `d` retried after a failure, an empty search result.
- Test PDFs are generated with reportlab at test time, not committed.
