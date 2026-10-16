# Milestone 2 — Spaced review and resumable sessions (design)

Date: 2026-10-16 · Status: awaiting owner approval

Builds milestone 2 of [Ebb — Requirements & System Design](../../../Ebb%20—%20Requirements%20&%20System%20Design.pdf):
"Persistence and spaced review (FR-6, FR-7)". It extends the milestone 1 core loop
([spec](2026-10-15-core-loop-design.md)), and takes in the milestone 2 items from
[m1-followups.md](../m1-followups.md) and review decisions E3, E8 and E9 from
[plan-reviews.md](../../plan-reviews.md).

## Intent

Milestone 1 can teach a concept once. Ebb's whole promise is the second part:
bringing an idea back just before it is forgotten. Milestone 2 makes coming back
work. You open `ebb study` on a later day, Ebb tells you what has faded and reviews
it first, and if you were interrupted it lets you pick up the exact question you
left. The owner tried milestone 1 and enjoyed it; this milestone is what turns a
quiz into a study habit.

## Goals

1. **Due reviews come first.** `ebb study` greets you with how many ideas are due,
   then reviews them, most faded first.
2. **Sessions run until you quit.** No three-concept cap. When reviews run out, it
   teaches new concepts, with no limit.
3. **Subjects grow.** When every concept has been learned and nothing is due, Ebb
   asks the LLM for further, more advanced concepts in the subject and teaches those.
4. **Review questions are always fresh.** A concept never repeats a question it has
   already asked you, in any session.
5. **Resume.** If the terminal is closed or Ebb crashes mid-session, the next
   `ebb study` within 24 hours asks whether to resume, then continues at the same
   question. Typing `q` is a deliberate end, not a pause (owner's decision).
6. **Your time zone.** Dates are shown in the time zone from `EBB_TIMEZONE`, falling
   back to the computer's.
7. **Hardening.** The database itself refuses cross-user rows; two terminals cannot
   overwrite each other's progress; a duplicate answer leaves no stale feedback.

## Non-goals (later milestones or out of scope)

Reminders or notifications (design decision D4); "quiz ahead" practice of concepts
that are not due; uploads and citations (M3); the FastAPI backend, streaming, the
per-user LLM quota (M4); flashcards and exams (M5); auth and Supabase (M6).

## Anti-goals (would be failures even if everything "works")

- Losing or double-counting progress: a resume that re-grades an answer, a migration
  that drops data, two terminals overwriting each other.
- Reviews silently piling up behind new material.
- The LLM deciding anything about scheduling or order. All of that stays in code.
- Existing milestone 1 data becoming unusable. The owner already has a subject with
  progress in their database.

## Decisions made by the owner

| Topic | Decision |
|---|---|
| What comes first on a later day | Due reviews first |
| Nothing due | Teach new concepts |
| New concepts per session | No limit |
| Session length | Until you type `q` |
| Everything learned and nothing due | Ask the LLM for more, more advanced concepts and teach those |
| Typing `q` | Ends the session with the summary; it is not resumable |
| Terminal closed or crash mid-session | Next `ebb study` asks to resume |
| Resume window | 24 hours from when you stopped |
| Review questions | Always new ones, never repeating earlier questions |
| Time zone | Set once with `EBB_TIMEZONE`; if unset, the computer's time zone |
| Due count | Shown at the start of `ebb study` |
| Hardening | Include all three deferred items |

Unchanged from milestone 1: within a concept, it moves on after two correct in a row
or four questions; Leitner stages, intervals (1, 3, 7, 16, 35 days), the tide formula
and "promote only when due" (E2, E5) stay exactly as they are; the summary appears
when the session ends.

## Behaviour

### Starting a session

1. Startup does what milestone 1 does (settings, pool, migrations, profile), plus:
   resolves the time zone, closes stale sessions and prunes old checkpoints (see
   *Sessions*).
2. If this subject has a resumable session (see *Resume*), ask:
   `You stopped on DNS resolution, question 2, 3 hours ago. Resume? [Y/n]`.
3. Otherwise, greet:
   - due > 0: `Welcome back — 3 ideas are ebbing. Reviewing those first.`
   - due = 0 and unlearned concepts exist: `Nothing is due. Let's learn something new.`
   - due = 0 and all learned: `You've learned every concept here. Finding more advanced ones…`

   "Due" means `next_review_at <= now` (unchanged from milestone 1).

### Picking the next concept (replaces `routing.pick_next_concept`)

Among concepts not yet studied in this session:

1. **Due reviews** (stage 1–5 and due), lowest tide first; ties broken by `position`.
2. Then **new concepts** (stage 0), in `position` order.
3. Then, if neither exists, **extend the subject** (below) and pick again.

Learned concepts that are not due are never picked. Practising them early is a
non-goal.

### Extending a subject

A new graph node `extend_subject` calls a new tutor method
`extend_concepts(subject_name, existing: list[ConceptDraft]) -> Generated[list[ConceptDraft]]`.
It asks for 3–5 further concepts that build on the existing ones and are more
advanced, with no duplicates. It needs its own prompt (`extend_concepts.md`) and a
schema (`ConceptExtension`, 3–5 items, same name rules as `ConceptDraft`).

- New concepts are saved at stage 0 with positions after the current last one.
  Names that duplicate an existing concept (case-insensitive) are dropped.
- If nothing new survives, or the call fails with `AllProvidersExhausted`, the
  session ends through `summarize` with a notice:
  `You're all caught up — next review {weekday date}.` (or the exhausted notice).
- A `ConfigError` (bad key or retired model) propagates as in milestone 1 (exit 2).

### Within a concept

Exactly as milestone 1, with one change: the question generator's avoid list is
every question you previously **answered** about this concept in **any** session
(questions joined through `attempts`, most recent first, capped at 40 prompts),
plus this session's. Generated questions you were never shown are not banned.

### Ending

The session ends when you type `q` (or Ctrl+C / end of input at a question), or
when extending finds nothing new. Ending sets `ended_at`, so that session is not
resumable. The summary lists every concept studied this session; "Next review"
dates use the resolved time zone.

What leaves a session resumable is everything that stops Ebb without reaching
`summarize`: the terminal window closed, the process or container killed, a crash,
or Ctrl+C while the tutor is thinking (milestone 1 already exits with code 130
there without ending the session).

### Resume

- A session is **resumable** when `ended_at is null` and `last_active_at` is within
  the last 24 hours. `last_active_at` is a new column, set when the session starts
  and on every recorded answer.
- Only the most recent resumable session per subject is offered.
- **Yes:** rebuild the graph with the Postgres checkpointer on that `thread_id`,
  read the pending question from the saved state (`graph.get_state(config).interrupts`),
  render it as the next question, and continue the normal loop. No answer is
  re-graded and nothing is double-counted.
- **No:** close that session (`ended_at = now`) and start a new one.
- If the saved state has no pending question (e.g. it crashed while grading), treat it
  as not resumable: close it and start fresh. Any answer that was fully recorded stays.

### Sessions and checkpoints

- **Stale sessions:** at startup, sessions with `ended_at is null` and
  `last_active_at` older than 24 hours get `ended_at = last_active_at`.
- **Checkpoint pruning (E8):** at startup, delete checkpointer data for threads whose
  session ended more than 7 days ago (`PostgresSaver.delete_thread`). Learning state
  lives in Ebb's own tables, so nothing a learner sees is lost.

### Time zone

- New setting `EBB_TIMEZONE` (an IANA name such as `Asia/Dhaka`). If set, it must be
  valid, otherwise `ConfigError`: `EBB_TIMEZONE 'X' is not a known time zone`
  (exit 2). If unset, use the computer's local time zone, found with the `tzlocal`
  package (`get_localzone_name()`, which maps Windows zones to IANA names); if that
  fails, `UTC`.
- The resolved name is saved to `profiles.timezone`, the column milestone 1 created
  for this.
- It is used for **displaying** dates and days: the `status` table, the summary's
  next-review column, the resume prompt and the all-caught-up notice. Due logic stays
  precise-time (see *Calls I made*).
- Inside Docker the "computer" is the container, which runs on UTC, so
  `.env.example` documents `EBB_TIMEZONE` as the way to get local dates there.
- Add the `tzdata` and `tzlocal` packages as dependencies so IANA names resolve, and
  the local zone can be named, on every OS and in the slim image.

## Technical design

### Checkpointer

- Add `langgraph-checkpoint-postgres` (latest stable). `PostgresSaver` runs on the
  existing pool, which already uses autocommit and `dict_row` as it requires.
  `setup()` runs at startup after Ebb's own migrations (idempotent).
- `build_graph` is unchanged in shape; the CLI passes a `PostgresSaver` instead of
  `InMemorySaver`. Graph tests keep using `InMemorySaver`; database tests cover the
  real saver.
- The checkpointer connects through the session pooler on Supabase later (E8); it
  is just the same pool locally.

### Concurrency (E3 and the lost update)

- **One terminal per session:** while a session runs, the CLI holds a session-level
  advisory lock keyed on the `thread_id` on a dedicated connection. A second
  terminal that tries to resume it gets
  `That session is open in another terminal.` and is offered a new session instead.
- **No lost updates:** grading applies to the concept's **current database row**,
  not the graph's snapshot. `record_attempt` locks the row (`select … for update`),
  applies `mastery.apply_grade` to it, writes the attempt and the update in one
  transaction, and returns the updated concept (or `None` for a duplicate).
  `record_attempt` therefore takes the grade and fills in the attempt's
  `stage_before` / `stage_after` from the locked row, so the attempt history is the
  database's truth. `update_mastery` stores what the database returns, and the
  graph's tally, streak and summary (`stage_after`, `tide_after`) use that returned
  concept. The `Store` protocol and `MemoryStore` change accordingly; `db` may
  import the pure `mastery` module.

### Duplicate answers (the stale grade)

When `record_attempt` reports a duplicate, `update_mastery` clears `grade` and
`lesson`, leaves the tally and stage untouched, and routing continues as after a
partial answer that did not count. The next question shows no feedback from the
earlier answer.

### Schema — `002_spaced_review.sql`

- `study_sessions.last_active_at timestamptz not null default now()`, back-filled
  from `started_at`.
- Ownership enforced by the database (E9): `unique (id, user_id)` on `subjects`,
  `concepts`, `study_sessions` and `questions`, and composite foreign keys on the
  children (`concepts (subject_id, user_id)`, `study_sessions (subject_id, user_id)`,
  `questions (session_id, user_id)` and `(concept_id, user_id)`, `attempts
  (question_id, user_id)` and `(concept_id, user_id)`), replacing the single-column
  foreign keys.
- Must apply cleanly to a milestone 1 database with data in it (the owner's), and
  keep it.

### Routing and graph changes

- `CONCEPTS_PER_SESSION` goes. After a concept is done the graph always picks again;
  the session ends only on quit, or when extending finds nothing.
- `pick_concept` uses the new order; when it finds nothing it routes to
  `extend_subject`, which routes back to `pick_concept` or to `summarize`.
- The graph grows from nine nodes to ten.

### Store additions

`count_due(user_id, subject_id, now)`, `get_subject(user_id, subject_id)`,
`add_concepts(user_id, subject_id, drafts) -> int` (returns how many were added,
after dropping duplicates), `asked_prompts(user_id, concept_id, limit)`,
`resumable_session(user_id, subject_id, now)`, `touch_session(user_id, session_id,
now)` (or done inside `record_attempt`), `close_stale_sessions(user_id, now)`,
`threads_to_prune(user_id, now)`, `set_timezone(user_id, name)`. Every method takes
`user_id` first and every statement filters on it (E1).

## Errors

Milestone 1's classes and messages are unchanged. New cases:

| Case | Handling | User sees |
|---|---|---|
| Invalid `EBB_TIMEZONE` | `ConfigError` at startup | one line naming the variable, exit 2 |
| Extending fails (`AllProvidersExhausted`) | end via `summarize` | the exhausted notice, then the summary |
| Extending finds nothing new | end via `summarize` | "You're all caught up — next review …" |
| Saved session has no pending question | close it, start fresh | the normal greeting |
| Session locked by another terminal | offer a new session | "That session is open in another terminal." |

## Testing

| Layer | What |
|---|---|
| Pure | the new pick order (due by lowest tide, then new by position, then none); routing without the session cap |
| Graph (offline) | a session that runs past three concepts until quit; due reviews before new concepts; nothing due → new; all learned → `extend_subject` → new concepts taught; extend returns only duplicates → ends with the all-caught-up notice; extend fails → exhausted notice; cross-session avoid list passed to `generate_questions`; the duplicate-answer path clears stale feedback |
| Database | `002` applies to a database holding milestone 1 data and keeps it; composite FKs reject a cross-user row; `record_attempt` applies grades to the current row (two stale snapshots in sequence both count); stale-session closing; 24-hour window boundary; checkpoint pruning |
| Resume (database) | run to a question, drop the graph, rebuild it with a new `PostgresSaver` on the same thread, read the pending question, resume, and finish; no attempt double-counted; a crashed state with no pending question is closed |
| Concurrency | a second advisory-lock holder on the same thread is refused |
| Time zone | valid name, invalid name (exit 2), unset → local; dates rendered in the resolved zone |
| CLI | greeting variants; resume prompt yes / no; "open in another terminal" |

CI unchanged: everything runs against Postgres with `EBB_REQUIRE_DB=1` and
`LANGGRAPH_STRICT_MSGPACK=true`.

## Done when

1. All tests pass locally and in CI; ruff clean.
2. With real keys in Docker: start `ebb study`, answer a question, then close the
   terminal window at the next one (or stop the container); run `ebb study` again →
   the resume prompt names that question; yes → it continues there. Separately,
   typing `q` ends the session and the next `ebb study` does not offer a resume.
3. Make a concept due by moving its `next_review_at` into the past with one SQL
   statement (documented in the README's development section); `ebb study` greets
   with the due count and reviews it first.
4. The owner's existing milestone 1 data is intact after the `002` migration.

## Calls I made (check these)

- **Due stays precise-time.** A concept is due at `next_review_at`, not "any time
  on its due day", and the time zone only affects how dates are shown. This keeps
  milestone 1's tested rule that tide is exactly 70% when a concept falls due. The
  alternative, Anki-style day boundaries, would change the mastery rules.
- **"Most faded first"** within due reviews means lowest tide.
- **Extending adds 3–5 concepts at a time**, and ends the session if none are new.
- **The resume window is measured from your last answer**, via a new
  `last_active_at`, not from when the session started.
- **The avoid list is capped at the 40 most recent prompts per concept**, to keep the
  prompt small.
- **"Previously asked" means answered**, so unseen batch questions can still appear.
- **Local time zone via `tzlocal`**, falling back to UTC.
- **Attempt history records the database row's stages**, not the graph's snapshot.
- **Greeting wording** as written above.
- **A second terminal is refused** rather than allowed to join the same session.

## Left to the builder

Exact prompt wording for `extend_concepts.md`; the advisory-lock key derivation;
how the CLI renders the resume prompt; test-fixture structure; and the exact SQL
for replacing foreign keys in `002`, as long as it is transactional and keeps
existing data.
