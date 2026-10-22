# Milestone 3 — follow-ups

Items found while building and reviewing milestone 3 that were deliberately left for
later. Earlier items: [m1-followups.md](m1-followups.md), [m2-followups.md](m2-followups.md).

## Providers and the eval (owner action)

- **Mistral is blocked, so there is no working fallback.** Every chat call returns HTTP 429 with
  code 1300 ("Rate limit exceeded") while listing models works, even with a new key: the account
  has no active free plan. Separately, Kaspersky Anti-Virus intercepts TLS to `api.mistral.ai` on
  the owner's PC (Python's certificate store then rejects the chain); a Kaspersky exclusion vs
  using `truststore` is still undecided. Until both are sorted, a Groq failure has nothing to fall
  back to, and `ebb eval build` (Mistral is the writer) can't run. While the plan is inactive,
  leave `MISTRAL_API_KEY` out of `.env`, or every Groq failure also waits on a Mistral 429.
- **Mistral 422 is classified as "retry".** An unprocessable-request error can't be fixed by a
  retry; classify it with the errors that stop at once.
- **Eval baseline: 70% (35 of 50), target 90%.** 14 grades were too lenient and 1 too harsh;
  about half of the disagreements are label noise (the writer's "partial" answers were in fact
  complete), the rest real leniency. The committed set was written by Groq `qwen/qwen3.8-27b`
  and graded by Groq `openai/gpt-oss-120b` (same provider, different model); rebuild it with the
  Mistral writer once Mistral works.
- **Stricter grading prompt and stricter eval writer: not done.** The owner chose only the code
  rule (a reported misconception caps a grade at partial). A stricter writer would cut the label
  noise; a stricter prompt would cut the real leniency.
- **Watch question quality from Groq.** In the live check a Groq-written MCQ had options that
  didn't answer its stem. Groq was the backup then and is the primary now.

## Safety (before milestone 4/6, when other people use Ebb)

- **A provider's safety refusal is treated as an ordinary failure**: it is retried and then
  falls back to Mistral. Detect a refusal, show a clear message, and never fall back.
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
- **A malformed dispute id** raises outside the pre-check (the CLI builds the id, so only a
  future API caller can hit it).
- Two terminals racing `ebb new` on one name create **two subjects with the same name**: there
  is no unique constraint on subject names.

## Trade-offs kept on purpose

- **A store refusal during a dispute marks the answer disputed**, so `d` is not offered again
  for it, even though nothing changed. Every refusal reason is permanent (already disputed,
  voided, no longer the latest answer, grade changed), so offering `d` again would only repeat
  the refusal; the cost is that a refusal can't be retried in that session.

## Tune against real notes

- **`MIN_RANK` (0.05)**: check retrieval on a real lecture PDF. Too high turns covered concepts into
  "General knowledge"; too low lets loosely related passages through.
- **The repeat rule for short questions**: an overlap of 0.6, or 0.45 with at least three
  shared words. Two short questions that share most words count as repeats and cost a web
  search. Consider a minimum word count before the overlap rule.
- The repeat stemmer misses -es/-ies plurals (it only misses a repeat).
- Repeat detection compares against the 40 most recent answered prompts; raise the cap if older
  repeats slip through.
- **Markdown `#` lines inside fenced code blocks are read as headings**, so a shell comment like
  `# ping the gateway` becomes the section "§ ping the gateway". Track fences in the chunker.
- **PDF headings come from the first line of each page.** On textbook PDFs with running heads or
  page numbers ("57", "Chapter 3 · Routing 58") every page gets a new heading, which splits
  chunks per page and adds noise to the digest. Fine for slides; ignore a first line that is only
  digits or that repeats on most pages.

## Terminal polish (the milestone 4 web UI replaces most of these flows)

- **A short answer of "d" or "D" can't be submitted**: `d` is always read as a dispute request.
  Treat it as a dispute only when one is possible, or accept `/d`. `q` has the same problem.
- **No progress indicator while a large PDF is read**, and pypdf's warnings ("EOF marker not
  found") leak to the terminal. Wrap the read in a status spinner and quiet the `pypdf` logger.
- **Notices are dropped when a dispute result is shown**, so "Switched to a backup model…" from
  a regrade that fell back never appears.
- **The dispute menu re-asks silently on bad input**; print `Type 1-4.`
- **The notes report ends "0 new:" with a trailing colon** when nothing is new, and concepts
  de-duplicated by name appear in neither list.
- **`eval build` prints nothing per case** (`eval run` now shows `Grading n/N…`), and a bad key
  there still shows a traceback.

## Behaviour worth revisiting

- **`extend_concepts` near-duplicates are still open** (see m2-followups): rewordings slip past the
  exact-name check.
- Web replacements are not checked against sibling notes questions in the same batch.
- An empty first search marks the concept searched, so it gets no web questions that session.
- If every provider fails during a citation retry, that batch becomes general-knowledge questions.
- Wrapped citation quotes lose their indent; the invalid-input hint omits `d`; "1 concepts" and
  "1 passages" plurals; web labels with an empty host or title fall back awkwardly.
- `_today()` is called twice in `eval run` (heading vs file name across midnight).

## Graph refactors deferred from `/simplify` (before the milestone 4 API)

Found by the `/simplify` review and left out because they reshape the graph right before a
merge. Worth doing before the API wraps the graph.

- **One place that fills state defaults.** New keys are read with `state.get(key, default)` in
  many nodes (for checkpoints saved by milestone 2); normalise once at the resume boundary
  instead.
- **One retry mechanism.** Question batches retry through a graph loop (`citation_retries`,
  `citation_failures`, `citation_failing`), grades and lessons inside `_verified` with
  per-call `keep` lambdas; unify them, ideally with the tutor returning only a citation fix.
- **A typed regrade result** instead of the `(Grade, bool)` tuple.
- **One candidate-run object** instead of the notes/web `generation_mode` flag threaded through
  `generate_questions`, `verify_citations` and `search_web`.
- **Summary tallies from the store** instead of correcting `tally` and `results` copies after a
  dispute.
- **Structured citation labels** (domain, title, heading, page) instead of `source_line`
  parsing the label string that `citations.py` built.
- **One `current_question_id`** instead of `pending_question_id` plus `retry_question`, and the
  lesson as `{text, citations}` instead of a separate `lesson_citations` key.

## Small leftovers from `/code-review` and its fixes

- A quote that crosses two line-break hyphens of different kinds (one real compound, one split
  word) matches neither form.
- A rewritten question's type and difficulty aren't enforced, so a retry can change a batch's
  multiple-choice/short mix.
- `ebb new` from a typed topic doesn't drop duplicate concept names (`create_subject` doesn't
  use `_insert_concepts`); switching would change behaviour, so it was left.

## Schema and tests (low priority)

- `grade_disputes` has no check tying `new_grade` null to outcome voided; no `subject_id` index
  on `document_chunks`.
- Concurrency and `create_subject_with_notes` transaction tests are smoke tests, not proof.
- Missing tests: both extra `new --notes` refusals, lesson citation rendering, a mixed
  notes/web batch, teaching a web question, `d` retried after a failure, an empty search result.
- Test PDFs are generated with reportlab at test time, not committed.
