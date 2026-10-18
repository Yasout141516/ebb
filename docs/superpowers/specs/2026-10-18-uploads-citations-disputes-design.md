# Milestone 3: Uploads, cited answers, web questions, disputes and the grading eval (design)

Date: 2026-10-18 · Status: approved by the owner

Builds milestone 3 of [Ebb — Requirements & System Design](../../../Ebb%20—%20Requirements%20&%20System%20Design.pdf):
"Uploads with cited answers, grade disputes, and the grading eval (FR-8, FR-9, NFR-2)".
It extends milestone 2 ([spec](2026-10-16-spaced-review-design.md)) and applies design
decisions D2 and D5 from [plan-reviews.md](../../plan-reviews.md). It also adds one feature
the owner asked for during brainstorming that the original plan doesn't have: questions
written from web pages when Ebb starts repeating itself. Where this document disagrees with
the PDF, this document wins.

## Intent

So far, Ebb writes and grades every question from the model's own knowledge. Milestone 3
lets it work from your actual course material. You add lecture slides, textbook chapters
or your own notes, Ebb finds the concepts in them, and its questions, grades and lessons
quote the page they came from. If you think a grade is wrong, you can dispute it and a
stricter second grader re-checks it. A grading eval measures how often the grader gets
grades right. Everything stays in the terminal. The web frontend is milestone 4.

The project is a portfolio piece about agent engineering. This milestone adds most of the
graph's agent behaviour: retrieval, a self-checking citation loop with bounded retries, a
human-in-the-loop dispute, a web-search step chosen by code, and a measured eval. The
owner chose to **keep code in control**. Code decides when to retrieve, search, retry or
regrade. The LLM writes questions, grades and teaches, and its citations are checked in
code.

## Goals

1. **Add notes to a subject.** You can attach PDF, TXT or MD files to a new subject or an
   existing one. The limits are 20 MB per file and 5 files per subject. A scanned PDF is
   refused (D2).
2. **Concepts from notes.** Adding notes finds the concepts they cover. Concepts the
   subject doesn't have yet join at stage 0. Concepts it already has keep their stage and
   are not duplicated under a new wording.
3. **Cited questions, grades and lessons.** When your notes cover a concept, the questions,
   short-answer grades and lessons about it quote your notes. Each citation shows the file,
   the page (or section) and the quoted sentence. Every citation is verified in code
   before you see it. If verification still fails after two retries, Ebb shows the
   "General knowledge · not from your notes" label instead.
4. **Fresh questions from the web.** When a newly written question repeats one you have
   already answered, Ebb drops it and writes a replacement from web pages found with
   Tavily, cited and verified the same way.
5. **Dispute a grade.** Right after a short-answer grade, you can dispute it. A stricter
   re-check can raise the grade, lower it, or void the answer if the question was
   ambiguous. The concept's stage is corrected to match.
6. **A grading eval with a number.** One command scores the grader against a frozen set of
   about 40 labelled answers and writes the agreement rate (target ≥ 90%) to a results
   file in the repo.

## Non-goals (later milestones or out of scope)

- OCR for scanned PDFs (D2).
- Removing or replacing an uploaded file.
- Keeping the original file. Only its extracted text and chunks are stored.
- Disputing an answer from an earlier session, or an MCQ grade.
- Real past-paper questions copied from the web.
- Web search on any trigger other than repeats.
- LangSmith.
- Suggested-reading links (FR-13, milestone 5).
- The web frontend and API (milestone 4).
- Supabase Storage and auth (milestone 6).

## Anti-goals (failures even if everything "works")

- A citation that points at the wrong page, or a quote that isn't in the source. A wrong
  citation is worse than none.
- Duplicate concepts after adding notes ("Subnetting" next to "IP Subnetting").
- A dispute that corrupts the schedule: a stage that doesn't match the final grade, or an
  answer counted twice.
- The LLM deciding when to search, retry or regrade.
- Sending your notes to the web-search service.
- Breaking the owner's existing "Computer Networks" data or the milestone 2 resume
  behaviour.
- An eval that quietly measures something easier than what it claims.

## Decisions made by the owner

| Topic | Decision |
|---|---|
| Kinds of notes | A mix: lecture slide PDFs, textbook chapters, own typed notes |
| Adding notes to an existing topic-only subject | Allowed. New concepts from the notes join at stage 0; existing concepts keep their stage and start citing the notes |
| What a citation shows | File, page and the quoted sentence(s) |
| When a dispute can be raised | Only right after the grade, before moving on |
| Can a re-check lower the grade? | Yes, either direction; the stage follows the new grade |
| "The question is ambiguous", upheld | The answer is voided: it doesn't count, the stage goes back, the question is never asked again |
| How much the LLM decides | Code stays in control (no tool-calling agent) |
| Eval answers | Generated answers with labels from the grade they were written to be; fully automatic, no hand-labelling |
| LangSmith | Skipped; the eval runs locally and writes results to the repo |
| Web search | Yes: write new questions from web pages |
| When web search runs | Automatically, when a fresh question repeats one already answered |
| Search service | Tavily (free 1,000 searches/month, no card) |

**Plan amendments these imply:**
- **NFR-2** changes from "30–50 hand-labelled answers in LangSmith" to "about 40 generated
  answers labelled with their intended grade, scored locally, results committed". The
  results file and README say plainly that the labels come from a generator model, not a
  person.
- **"Grounding stays simple: Postgres full-text search over uploaded notes"** still holds
  for notes. Web pages are an additional, separate source, used only for replacement
  questions.

## Behaviour

### Adding notes

Commands:

```
ebb new "Computer Networks" --notes notes/CSE321_Wk1-6.pdf [--notes notes/ch3.md …]
ebb add notes/CSE321_Wk1-6.pdf ["Computer Networks"]     # subject defaults to newest
ebb status ["Computer Networks"]                         # now also lists sources
```

`ebb new` with `--notes` builds the concept list from the notes only. It does not also
run the topic-only concept step. Without `--notes`, it behaves as it does today.

`ebb new … --notes` is **all-or-nothing for the whole command**:
1. Every file's checks and text extraction (steps 1–4 below) run before any LLM call. Any
   refusal stops the command with nothing created.
2. The concept calls then run file by file. Each call sees the concepts found in the
   earlier files as "existing", so no duplicates appear across files.
3. The subject, every document and every concept are saved in one transaction. If any
   concept call fails, nothing is created and running the same command again is safe.

`ebb new` (with or without `--notes`) now refuses a name that already exists, ignoring
case: `Computer Networks already exists — add notes to it with: ebb add <file>`. This
also keeps a retried command from creating a second subject.

Steps for each file, in order. Any failure stops that file and stores nothing for it:

1. **Checks.** The extension must be `.pdf`, `.txt` or `.md` (case-insensitive). Size
   must be ≤ 20 MB. The subject must have fewer than 5 files. The same content
   (SHA-256) must not already be in this subject.
2. **Privacy line**, printed once per command:
   `Don't upload sensitive documents. Files are processed by an AI model to write questions and cite answers.`
3. **Extract text.**
   - PDF: pypdf, page by page. If the average is under 50 characters per page, refuse
     with D2's message: `No text found — this looks like a scanned PDF. Export it as
     text, or upload the PDF your slides were made from.`
   - TXT and MD: decode as UTF-8 (with or without a BOM). If that fails, refuse with
     `Couldn't read <file> as text — save it as UTF-8.`
   - Empty text: refuse with `<file> has no text in it.`
4. **Chunk** (see *Technical design*), keeping page numbers (PDF) and the nearest heading.
5. **Find concepts** with one LLM call (see *Concepts from notes*).
6. **Save** the document, its chunks and the new concepts in one transaction.
7. **Report:**
   ```
   CSE321_Wk1-6.pdf · 38 pages · 112 passages
   9 concepts in these notes — 2 already here (Subnetting, DNS), 7 new:
     1. ARP — …
     2. NAT — …
   ```

If the concept call fails (`AllProvidersExhausted`), nothing is saved and Ebb prints the
exhausted notice. You can run the same command again. A `ConfigError` exits with code 2,
as it does today.

Docker: `docker-compose.yml` mounts `./notes` (git-ignored) read-only at
`/home/ebb/notes`. You put files there and run
`docker compose run --rm ebb add notes/<file>`. The README explains this. Running with
`uv run ebb add <any path>` also works.

Limit messages: `<file> is 26.4 MB — over the 20 MB limit. Split it into chapters, or
upload just the pages you're studying.` · `Computer Networks already has 5 of 5 files.` ·
`<file> is already in Computer Networks.` · `Ebb reads PDF, TXT or MD files.`

### Concepts from notes

A new tutor method, `concepts_from_notes(subject, existing concepts, digest)`, returns
between 3 and 12 concepts. Each one is either **new** (name + description) or **the same
as** an existing concept, named exactly as it appears in the list Ebb sent.

- The **digest** is the document's headings plus the opening of each chunk, sampled evenly
  across the document, capped at about 30,000 characters. This keeps a textbook chapter
  within the backup model's context.
- Code checks every "same as" against the existing names (exact match, ignoring case and
  surrounding spaces). A "same as" that names nothing real is dropped, not added. New
  concepts still go through the existing exact-name de-duplication (`new_concepts`).
- New concepts are appended at stage 0, after the current last position.
- Existing concepts need no change. They are grounded at question time by retrieval
  (below), whether or not the model listed them.

This fixes the near-duplicate risk from [m2-followups.md](../m2-followups.md) for notes.
`extend_concepts` is unchanged, and that follow-up stays open for it.

### Retrieving notes for a concept

After a concept is picked, a new code node `retrieve_notes` runs a Postgres full-text
search over the subject's chunks. The search terms come from the concept's name and
description, and any term may match (OR, not AND). Results are ranked with `ts_rank_cd`.
Ebb keeps the top 4 chunks above a minimum rank. If none pass, the concept is
"not covered" and its questions are general knowledge, as today. The chunks (id, file,
page, heading, text) go into graph state, so a resumed session uses the same ones.

### Citations and `verify_citations`

The model never sees chunk UUIDs. Sources are shown to it with short labels
(`N1`…`N4` for notes, `W1`…`W5` for web pages), and code maps the labels back. Each
generated question, grade or lesson may carry up to 3 citations, each a source label and
a quote of at most 200 characters.

A citation is valid only if all of these hold:
1. Its label is one of the sources given to the model for this call. An unknown label
   fails.
2. Its quote appears in that source's text. Whitespace, letter case and typographic quotes
   and dashes are normalised before matching.
3. The quote is not empty.

Rules:
- **Questions (graph node).** `generate_questions → verify_citations`. If any question in
  the batch has an invalid citation, the graph goes back to `generate_questions` with a
  note naming the failures, at most twice. After two retries, invalid citations are
  removed. A question that cited notes but has no valid citation left becomes general
  knowledge. Citation retries do not count towards milestone 1's
  "two generation calls per concept" limit.
- **Missing citations.** For a concept that has notes, a question that cites nothing
  counts as a failure, the same as an invalid citation, and triggers the batch retry.
  One retry rewrites all five questions in a single call, which is affordable. Web-mode
  questions must cite too.
- **Grades and lessons (inside their nodes).** `grade_answer` (short answers) and `teach`
  retry only on an **invalid** citation, with the same two-retry budget. A grade or
  lesson that cites nothing is not retried, because that would cost up to three calls
  per answer on free-tier limits. Instead, it shows the **question's** verified
  citations. If, after the retries, a grade or lesson still has no valid citations of
  its own, it is kept and shows the question's citations. Only when the question itself
  is general knowledge does the "General knowledge" label appear.
- **MCQ.** Code still grades it. The grade card shows the question's own citations.
- **No notes.** For a subject without documents, notes questions, grades and lessons
  look as they do in milestone 2: no citations and no label. Web replacement questions
  (below) still apply to every subject, notes or not, because their only trigger is a
  repeat. On a topic-only subject they are the only questions with a source line and
  citations. When the subject has notes and a question,
  grade or lesson ends up without a valid citation, it shows
  `General knowledge · not from your notes`. A broken or empty citation is never shown.

When sources are given, the grader is told: the notes are the authority. If the reference
answer and the notes disagree, grade against the notes.

**Display** under the grade (and under the lesson, if it has its own citations):

```
◐ Partial — you named the network and broadcast addresses but not why they're unusable.

From your notes
  [1] CSE321_Wk1-6.pdf · p. 12
      "The first address identifies the network and the last is the broadcast address,
       so neither can be assigned to a host."
```

TXT and MD sources have no pages, so they show the heading instead:
`networks.md · § Subnet masks`. Web sources show `From the web` with
`domain · page title`. When the question is asked, a dim line under it names where it
came from (`From your notes · p. 12`, `From the web · cloudflare.com`). The quote waits
for the grade card.

### Web questions when Ebb repeats itself

After `verify_citations` passes a batch, code compares each new question's prompt with
every prompt you have answered on this concept, in any session, plus this session's. A
question **too close** to an earlier one is dropped. "Too close" means a normalised
word-overlap score at or above a threshold, set by the builder and covered by tests with
real paraphrase examples.

If any were dropped, the graph goes to a new code node `search_web`, provided these all
hold:
- `TAVILY_API_KEY` is set;
- this concept hasn't been searched in this session;
- an earlier search in this session hasn't failed.

`search_web` runs one Tavily basic search for `"<subject name> <concept name>"`, asking
for up to 5 results with their page text. Only those two names leave the machine. Then
`generate_questions` runs in **web mode**: it writes exactly as many questions as were
dropped, from the pages (`W1`…`W5`), and must cite them. They go through
`verify_citations` under the same rules. After the retries, a web question without a
valid citation is **dropped**, not shown as general knowledge. These questions are
tagged `web`, and their page excerpts stay in state, so grading and teaching that
question see and can cite the same text.

If the search is skipped or fails (no key, quota used up, network error), the repeats are
simply dropped. The first time this happens in a session, Ebb shows
`Web search unavailable — skipped repeated questions.` If the batch ends up empty, the
existing routing applies: generate again if the concept has generation calls left,
otherwise move on.

Page text is untrusted. Every prompt that includes it fences it with markers and tells
the model to treat it as reference material only, the same way student answers are
handled today.

### Disputing a grade

**When.** Ebb shows a grade together with the next question, after the answer. That
prompt now also accepts `d` when the grade shown is for a short answer graded by the LLM
and hasn't been disputed yet. The hint line reads
`Type your answer, d to dispute that grade, or q to stop`. `d` is accepted only there:
the previous answer must be the latest attempt on its concept and must have been
answered in this session.

**Raising it.**
```
Why do you think the grade is wrong?
  1. My answer is correct
  2. The question is ambiguous
  3. My notes say something different
  4. Something else
› 3
Explain (optional, Enter to skip) › slide 14 says /31 is allowed on point-to-point links
Re-checking…
```
The CLI sends `{"dispute": {"reason", "explanation"}}` as the reply to the pending
question. Ctrl+C or end of input in the dialog cancels the dispute and returns to the
question. If Ebb is stopped while re-checking, resume works as in milestone 2: the prompt
names the question, and **Yes** finishes the re-check once.

**Re-check.** A new graph node, `regrade`, calls a new tutor method `regrade(...)` with a
stricter prompt (`regrade.md`). The model receives:
- the question and reference answer;
- your answer;
- the first grade and its explanation;
- your reason and explanation, fenced as untrusted text: a claim to check, never an
  instruction;
- the same sources the first grade had.

It returns a grade, an explanation, citations, and `question_ambiguous`. Citations go
through `verify_citations` with two retries. If the call fails (`AllProvidersExhausted`),
the dispute is not recorded, Ebb prints `Couldn't re-check right now — the grade stands.
Try again with d.`, and the question is shown again.

**Outcome (decided in code):**

| Condition | Outcome | Effect |
|---|---|---|
| Reason is "ambiguous" and `question_ambiguous` is true | **voided** | The answer stops counting: the concept is restored to its state before that answer. The question stays in the avoid list, so it is never asked again. |
| The new grade differs from the first | **changed** | The attempt's grade becomes the new grade. The concept is recomputed with `apply_grade(state before the answer, new grade, original graded_at)`. |
| Otherwise | **upheld** | Nothing changes. |

The result prints above the re-shown question, for example:
`Re-check: raised to Correct — Subnetting back to ●●●○○ level 3.` /
`Re-check: lowered to Incorrect — …` / `Re-check: the question was ambiguous — that
answer no longer counts.` / `Re-check: the grade stands — <explanation>`, followed by any
citations. A lesson already shown stays shown. If a lowered grade means you would now get
a lesson, no new lesson is generated. Within-concept routing (streak, difficulty) is not
replayed. The session tally and summary use the corrected grade, and a voided answer
leaves the tally.

Each answer can be disputed once. Every dispute is saved in `grade_disputes`, whatever
the outcome.

### The grading eval

```
cd backend
uv run ebb eval build      # once: writes evals/grading.jsonl (commit it)
uv run ebb eval run        # any time: scores the grader, writes evals/results/<date>-<model>.md
```

**Building the set** (`eval build`, run once, then frozen and committed):
- About 40 cases on general Computer Networks topics, plus about 10 grounded in
  `backend/evals/sample_notes.md`. That file is a short, original networking notes file
  written for the repo. The owner's own notes are never copied into the public repo.
- For each case, the **writer** model generates a short-answer question, a reference
  answer, and one student answer written to be a target grade (correct, partial or
  incorrect, about a third each), plus a one-line reason. The intended grade is the
  label. The writer is the **Groq** model (`EBB_FALLBACK_MODEL`), so the grader (Gemini)
  isn't grading text from its own model family. `eval build` needs `GROQ_API_KEY`.
- Each case records: id, question, reference, answer, label, the writer's reason, grounded
  sources (if any), and the writer model.
- `eval build` refuses to overwrite an existing set unless given `--force`.

**Running** (`eval run`):
- Runs every case through the real `grade` call on the **primary model only**, with no
  fallback, so one run measures one model. Calls are paced to stay inside free-tier rate
  limits.
- Reports the agreement rate against the ≥ 90% target, a 3×3 confusion table, "too
  harsh" and "too lenient" counts, and each disagreement (question, answer, label, grade,
  explanation).
- For grounded cases, also reports how many grades came back with a valid citation.
- Adds a **disputes** line from the database: disputes raised, changed, voided, upheld.
  These are counts only, never the answer text, because the results file is committed.
- Writes the report to `backend/evals/results/<YYYY-MM-DD>-<model>.md` and prints the
  summary. It always exits 0. The score is a number to watch, not a gate.
- It is not run in CI, because it spends free-tier quota. Its scoring code is unit-tested
  with `FakeTutor`.

## Technical design

### Graph

```
load_learner → pick_concept → retrieve_notes → generate_questions ⇄ verify_citations
     verify_citations → search_web → generate_questions (web mode) ⇄ verify_citations
     verify_citations → ask_question
ask_question → grade_answer → update_mastery → teach → …        (as milestone 2)
ask_question → regrade → ask_question                            (dispute)
pick_concept → extend_subject → pick_concept                     (as milestone 2)
```

New nodes: `retrieve_notes`, `verify_citations`, `search_web` and `regrade`, which takes
the graph from ten nodes to fourteen. All routing stays in `routing.py` as pure
functions. New state fields hold only JSON values:
- `sources`: retrieved notes chunks;
- `web_sources`: pages from the search;
- `citation_retries`;
- `generation_mode`: `"notes"` or `"web"`;
- `web_searched` and `web_failed`;
- `replacements_needed`;
- `dispute`: the pending reason and explanation;
- `dispute_result`.

### Chunking (open decision from the plan, settled here)

- Text is split into paragraphs, then packed into chunks of **about 1,200 characters
  (target), never more than 2,000**, with **about 200 characters of overlap**, taken from
  the end of the previous chunk at a sentence boundary where possible.
- PDF chunks may cross a page boundary. They record `page_start` and `page_end`, and the
  citation shows the page the quote is on: for each chunk, code keeps the character
  offset where each page starts.
- Headings: MD uses the nearest preceding `#` heading. For PDF, a short first line of a
  page (≤ 80 characters, no final full stop) counts as that page's heading, which fits
  slides. TXT has no headings.
- A slide page with less text than the target becomes its own chunk. Chunks never merge
  across a heading change.

### Schema: `003_notes_citations_disputes.sql`

Every new table carries `user_id`, with composite foreign keys to its parent (as in E9).
The migration must apply cleanly to the owner's database and keep its data.

- `documents`: `id`, `user_id`, `subject_id`, `filename`, `kind` (`pdf`/`txt`/`md`),
  `size_bytes`, `page_count` (null for text), `sha256`, `created_at`;
  `unique (subject_id, sha256)`.
- `document_chunks`: `id`, `user_id`, `document_id`, `subject_id`, `ordinal`,
  `page_start`, `page_end`, `page_offsets` (jsonb), `heading`, `content`, and
  `search_vector tsvector generated always as (to_tsvector('english', coalesce(heading,
  '') || ' ' || content)) stored`, with a GIN index on `search_vector`.
- `questions`: add `source text not null default 'general'` (`notes` / `web` /
  `general`; the plan's `is_general_knowledge` is `source = 'general'`) and
  `citations jsonb not null default '[]'`.
- `attempts`: add `citations jsonb not null default '[]'`, `concept_before jsonb` (stage,
  next_review_at, last_reviewed_at, review_count at the moment of grading; null for
  attempts recorded before this migration, which can't be disputed anyway), and
  `voided_at timestamptz`.
- `grade_disputes`: `id`, `user_id`, `attempt_id` (unique; composite FK to attempts),
  `reason` (`correct` / `ambiguous` / `notes_differ` / `other`), `explanation`,
  `original_grade`, `new_grade` (null when voided), `outcome` (`upheld` / `changed` /
  `voided`), `regrade_explanation`, `citations`, `model_used`, `created_at`.
- A citation in jsonb is `{type: "chunk" | "web", id: <chunk uuid | url>, label, page,
  quote}`. The label and quote are kept as a snapshot, so history still reads correctly
  if anything changes later.

Voided attempts are excluded from anything that counts answers. `answered_prompts` still
includes them, which is what keeps a voided question from being asked again.
`record_answer` fills `concept_before` from the locked row.

### Store additions

Every method takes `user_id` first:
- `add_document(user_id, subject_id, doc, chunks, concepts) -> AddResult`: one
  transaction; enforces the 5-file and duplicate rules under a row lock on the subject.
- `list_documents(user_id, subject_id)`.
- `search_chunks(user_id, subject_id, query, limit) -> list[Chunk]`.
- `resolve_dispute(user_id, attempt_id, dispute, outcome, new_grade, now) -> Concept`:
  locks the concept row; checks the attempt is the concept's latest non-voided attempt
  and isn't disputed yet (otherwise `LookupError`/`ValueError`); applies the outcome;
  writes the `grade_disputes` row; returns the corrected concept.
- `dispute_counts(user_id) -> dict`.
- `save_questions` and `record_answer` now also store `source`, `citations` and
  `concept_before`.

`MemoryStore` gets the same methods. Its `search_chunks` is a simple term-overlap
stand-in, and the real ranking is tested against Postgres.

### Tutor additions

- `concepts_from_notes`.
- `generate_questions` gains optional sources, a web mode with a count, and the previous
  attempt's failures.
- `grade` and `teach` gain optional sources.
- `regrade`.
- `write_eval_case` (writer model only).

New prompts: `concepts_from_notes.md`, `regrade.md`, `write_eval_case.md`. The existing
prompts gain a sources section that is left out when there are no sources. Schemas gain
`CitationDraft {source, quote}` and the new result types.

### Web search

A small `WebSearch` protocol with one Tavily implementation and a `FakeWebSearch` for
tests. Its errors map to "search failed", never to a crash. New optional setting:
`TAVILY_API_KEY`, added to `.env.example`.

### Dependencies

Latest stable versions, checked online at planning time, as the owner prefers:
- `pypdf` for PDF text;
- the Tavily client (`tavily-python`, or `langchain-tavily` if it's the better fit);
- nothing new for full-text search.

## Errors

The milestone 1 and 2 classes and messages are unchanged. New cases:

| Case | Handling | User sees |
|---|---|---|
| Wrong file type / too big / 5 of 5 / duplicate / scanned / not UTF-8 / empty | refuse before the LLM; nothing stored; exit 1 | the messages in *Adding notes* |
| File not found / unreadable | exit 1 | `Can't open <path>.` (in Docker: plus a hint about the `notes/` folder) |
| Concept call fails while adding | nothing stored; exit 1 | exhausted notice |
| Citations still invalid after 2 retries | drop citations (notes) or drop the question (web) | General-knowledge label, or a shorter batch |
| Tavily missing / quota / error | skip web for the session | one notice |
| Re-check fails | dispute not recorded; question re-shown | `Couldn't re-check right now — the grade stands. Try again with d.` |
| `d` when nothing can be disputed | ignored with a hint | `Only short-answer grades from this session can be disputed.` |
| `eval build` without `GROQ_API_KEY` | exit 2 | `eval build needs GROQ_API_KEY (the writer model).` |

## Testing

| Layer | What |
|---|---|
| Pure | chunker (paragraph packing, size cap, overlap, page offsets, MD/PDF headings, slide pages); text checks (D2 threshold, encodings); `verify_citations` (unknown label, quote absent, normalisation, empty quote, max 3); repeat detection on real paraphrase pairs and true negatives; dispute outcome table; concept merge ("same as" valid/invalid, near-duplicates); eval scoring (agreement, confusion, harsh/lenient) |
| PDF fixtures | small committed PDFs: a slide-style deck, a text page, an image-only page (scanned) |
| Graph (offline, FakeTutor + FakeWebSearch) | notes-covered concept → cited questions; citation fails once then passes; fails three times → general knowledge; repeats → `search_web` → web replacements; web citations fail → dropped; no key → notice and shorter batch; empty batch routing; dispute raised/changed/lowered/voided/upheld; re-check exhausted; tally and summary corrected; resume while re-checking finishes once |
| Database | `003` applies to a milestone 2 database with data and keeps it; composite FKs reject cross-user rows in the new tables; `add_document` enforces 5 files and duplicates under concurrency; full-text ranking finds the right chunk and ignores unrelated ones; `resolve_dispute` restores `concept_before` exactly; a second dispute on the same attempt is refused; voided attempts excluded from counts but kept in `answered_prompts` |
| CLI | `new --notes`, `add` success report and each refusal; `status` lists sources; `d` dialog, cancel, hint when not allowed; citation rendering for PDF pages, MD headings and web |
| Tutor | prompt rendering with and without sources; schemas for the new results; eval writer/grader wiring |

CI is unchanged: Postgres with `EBB_REQUIRE_DB=1` and `LANGGRAPH_STRICT_MSGPACK=true`. No
test calls a real LLM or Tavily.

## Done when

1. All tests pass locally and in CI, and ruff is clean.
2. In Docker with real keys: `ebb add notes/<a real lecture PDF> "Computer Networks"`
   reports its passages and concepts with no duplicates of existing ones, and
   `ebb status` lists it.
3. `ebb study` on a concept the PDF covers shows a question with "From your notes", and
   the grade card quotes a sentence that is really on the cited page. A concept the notes
   don't cover shows "General knowledge · not from your notes".
4. A scanned PDF and a 21 MB file are refused with the D2 and size messages.
5. With `TAVILY_API_KEY` set, a "From the web" question appears once repeats are
   detected. To force this, seed a concept's history with answered questions the model
   is likely to write again, using SQL documented in the README's development section.
6. Disputing a short-answer grade with `d` prints a re-check result, and `ebb status`
   shows the corrected stage.
7. `uv run ebb eval build` and `uv run ebb eval run` produce a committed dataset and
   results file with an agreement rate.
8. The owner's existing data is intact after `003`.

## Calls I made (check these)

- **Commands:** `ebb new … --notes FILE` (repeatable), `ebb add FILE [SUBJECT]`; sources
  listed in `ebb status`, not in a new command.
- **`ebb new … --notes` is all-or-nothing**: all files are checked before any LLM call,
  and the subject, documents and concepts are saved in one transaction. `ebb new` now
  refuses a subject name that already exists.
- **A notes question with no citation triggers a retry; a grade or lesson with none
  doesn't** (it shows the question's citations instead), to save free-tier calls.
- **Web replacement questions apply to topic-only subjects too.**
- **Docker reads files from a git-ignored `notes/` folder** mounted read-only.
- **The original file isn't kept.** Only extracted text and chunks are stored.
- **Adding a file is all-or-nothing.** If the concept call fails, nothing is saved.
- **A subject created with `--notes` gets its concepts only from the notes.**
- **3–12 concepts per file, from a digest capped at about 30,000 characters.**
- **The concept merge uses the model's "same as" answer, checked in code.** It fixes
  near-duplicates for notes only; `extend_concepts` is unchanged.
- **Chunking:** about 1,200 characters, 2,000 at most, about 200 of overlap; headings as
  described. This settles the plan's open decision.
- **Retrieval:** any-term full-text match on the concept's name and description, top 4
  above a minimum rank, retrieved once per concept per session.
- **Quotes are verified in code**, not just chunk IDs; at most 3 citations of up to 200
  characters each.
- **Short source labels** (`N1`, `W1`) instead of UUIDs in prompts.
- **Notes win** over the reference answer when they disagree.
- **The "General knowledge" label appears only for subjects that have notes.** Topic-only
  subjects look exactly as they do now.
- **A web question with an invalid citation is dropped**, while a notes question keeps
  going as general knowledge.
- **One web search per concept per session**, and none after a failure; the query is the
  subject and concept names only.
- **Disputes use `d` at the next question's prompt**, where the grade is shown today.
- **A corrected grade doesn't replay routing** (streak, difficulty) and doesn't generate a
  new lesson.
- **The re-check uses the primary model** with a stricter prompt, with the normal fallback.
- **Eval:** about 40 general cases plus about 10 grounded in a committed sample notes file;
  the Groq model writes, the primary model grades with no fallback; dispute stats are
  counts only; not in CI; never fails the build.

## Left to the builder

- Exact prompt wording.
- The full-text minimum rank and the repeat-detection threshold, tuned with tests.
- The digest sampling.
- Pacing for `eval run`.
- The exact Tavily client and parameters, within the free tier.
- How `ask_question` keeps the same pending question across a dispute.
- Test-fixture structure.
- The exact SQL for `003`, as long as it is transactional and keeps existing data.
