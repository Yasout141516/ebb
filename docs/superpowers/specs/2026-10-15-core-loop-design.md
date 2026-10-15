# Milestone 1 — Core quiz loop (design)

Date: 2026-10-15 · Status: approved · updated during planning (versions, `position` column, `Generated` results, provider loop)

Builds milestone 1 of [Ebb — Requirements & System Design](../../../Ebb%20—%20Requirements%20&%20System%20Design.pdf):
the core loop in the terminal — topic → concepts → questions → grade → mastery
update → routing (FR-1 to FR-5, typed topics only). Applies review decisions
E2, E3, E4, E5, E6 and E9 from [plan-reviews.md](../../plan-reviews.md).

## Goals

- `ebb new "<topic>"` creates a subject and its concepts; `ebb study` runs a quiz
  session against a real LLM; `ebb status` shows each concept's tide and stage.
- Learning state persists in Postgres from day one; restarting loses nothing
  except the in-progress session.
- The whole thing runs with Docker Compose and no local setup.
- `pytest` passes offline (no API key, no network) and in CI.

## Non-goals (later milestones)

Uploads, retrieval and citations (M3); `PostgresSaver` checkpointing and session
resume (M2); spaced-review scheduling across days beyond what `update_mastery`
writes (M2); FastAPI, streaming, per-user quota (M4); flashcards and exams (M5);
auth (M6).

## Decisions from brainstorming

| Decision | Choice |
|---|---|
| Versions | latest stable at build time: Python 3.14, Postgres 18.6, uv 0.13 |
| Where state lives in M1 | Postgres from day one (not in-memory) |
| DB access and schema changes | psycopg 3 + plain SQL migrations, no ORM |
| CI | GitHub Actions running pytest against a Postgres service |
| LLM boundary | a `Tutor` interface; tests use a `FakeTutor` |

## Layout

```
ebb/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── .github/workflows/ci.yml
└── backend/
    ├── Dockerfile
    ├── pyproject.toml              uv-managed; console script `ebb = ebb.cli:app`
    ├── db/init/01-test-db.sql      creates ebb_test on first compose start
    ├── src/ebb/
    │   ├── cli.py                  Typer + Rich commands
    │   ├── config.py               pydantic-settings
    │   ├── mastery.py              Leitner rules + tide (pure)
    │   ├── routing.py              routing decisions (pure)
    │   ├── grading.py              MCQ grading (pure)
    │   ├── domain.py               dataclasses shared by every layer (pure)
    │   ├── store.py                Store protocol (persistence interface)
    │   ├── ui.py                   Rich rendering and input parsing
    │   ├── graph/state.py          QuizState
    │   ├── graph/nodes.py          node functions
    │   ├── graph/build.py          build_graph(tutor, repo, checkpointer)
    │   ├── llm/schemas.py          Pydantic models for LLM I/O
    │   ├── llm/tutor.py            Tutor protocol, LangChainTutor, error classes
    │   ├── llm/prompts/*.md        one prompt per task
    │   ├── db/pool.py              psycopg_pool connection pool
    │   ├── db/migrate.py           migration runner
    │   ├── db/repo.py              repository functions (every one takes user_id)
    │   └── db/migrations/001_init.sql
    └── tests/
```

Pure modules (`mastery`, `routing`, `grading`) import nothing from `db`, `llm`
or `graph`.

## Configuration

`config.py` reads from the environment (and `.env` locally):

| Variable | Required | Default |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql://ebb:ebb@localhost:5432/ebb` |
| `GOOGLE_API_KEY` | for `new`/`study` | — |
| `GROQ_API_KEY` | no; enables fallback | — |
| `EBB_PRIMARY_MODEL` | no | `gemini-2.5-flash` |
| `EBB_FALLBACK_MODEL` | no | `llama-3.3-70b-versatile` |
| `EBB_DEV_USER_ID` | no | `00000000-0000-0000-0000-000000000001` |

Model defaults must be checked against the providers' current free-tier models
when building. On startup the CLI upserts a `profiles` row for the dev user
(timezone `UTC`).

## Docker

- **`db`**: `postgres:18.6-alpine`; user/password/db `ebb`; named volume
  `ebb-pgdata` mounted at `/var/lib/postgresql` (the Postgres 18 image layout); port `5432:5432`; healthcheck `pg_isready`; mounts
  `backend/db/init` to `/docker-entrypoint-initdb.d`.
- **`ebb`**: built from `backend/`; `profiles: ["cli"]`; `depends_on: db
  (service_healthy)`; `env_file: .env`; `DATABASE_URL` points at `db`;
  `stdin_open` and `tty` on; entrypoint `ebb`.
- **Dockerfile**: multi-stage. Stage 1 copies the `uv` binary from
  `ghcr.io/astral-sh/uv`, runs `uv sync --frozen --no-dev` into `/app/.venv`.
  Stage 2 is `python:3.14-slim` with the venv and source, running as a non-root
  user.

Usage:

```
docker compose up -d                              # starts db only
docker compose run --rm ebb new "Computer Networks"
docker compose run --rm ebb study
docker compose run --rm ebb status
```

Dev loop: `docker compose up -d` then `cd backend && uv run ebb study`.

## Data model — `001_init.sql`

All ids are `uuid` (`gen_random_uuid()` default unless noted). Every table has
`user_id uuid not null`. The foreign key from `profiles.id` to `auth.users` is
added only in the Supabase migration in M6, since local Postgres has no `auth`
schema.

| Table | Columns |
|---|---|
| `profiles` | `id` pk, `display_name text`, `timezone text not null default 'UTC'`, `created_at` |
| `subjects` | `id`, `user_id`, `name text not null`, `created_at` |
| `concepts` | `id`, `user_id`, `subject_id` fk, `name text not null`, `description text not null`, `position smallint` (the LLM's foundational-to-advanced order; breaks ties when picking), `stage smallint not null default 0 check (stage between 0 and 5)`, `next_review_at timestamptz null`, `last_reviewed_at timestamptz null`, `review_count int not null default 0` |
| `study_sessions` | `id`, `user_id`, `subject_id` fk, `mode text not null default 'quiz'`, `thread_id text not null unique`, `started_at`, `ended_at null` |
| `questions` | `id`, `user_id`, `session_id` fk, `concept_id` fk, `type text check (type in ('mcq','short'))`, `difficulty smallint check (1..3)`, `prompt text`, `options jsonb null`, `reference_answer text`, `created_at` |
| `attempts` | `id uuid pk` (no default — supplied by the caller), `user_id`, `question_id` fk, `concept_id` fk, `answer_text text`, `grade text check (grade in ('correct','partial','incorrect'))`, `feedback text`, `stage_before smallint`, `stage_after smallint`, `model_used text`, `graded_at timestamptz` |

`questions.options` for an MCQ is a list of exactly 4
`{text, is_correct, why}` objects with exactly one `is_correct = true`.

Indexes: `concepts (user_id, next_review_at)`, `attempts (user_id, graded_at)`,
`attempts (concept_id, graded_at)`.

**Migration runner** (`db/migrate.py`): creates `schema_migrations (version text
primary key, applied_at timestamptz)`, then applies each `migrations/*.sql` not
yet recorded, in filename order, each in its own transaction. Runs on every CLI
start.

## Mastery rules (`mastery.py`)

`INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 16, 5: 35}`. A concept is **due** when
`next_review_at is null or next_review_at <= now`.

`apply_grade(concept, grade, now) -> ConceptUpdate`:

| Concept state | correct | partial | incorrect |
|---|---|---|---|
| stage 0 (new) | → stage 1 | → stage 1 | → stage 1 |
| due, stage 1–5 | → stage + 1 (max 5) | stage kept, rescheduled | → stage 1 |
| not due | no change | no change | → stage 1 |

Whenever the stage or schedule changes: `last_reviewed_at = now`,
`next_review_at = now + INTERVAL_DAYS[new_stage]`, `review_count += 1`. "No
change" leaves all three untouched (the attempt is still logged with
`stage_after = stage_before`). This keeps the invariant
`next_review_at = last_reviewed_at + INTERVAL_DAYS[stage]` for every stage ≥ 1.

`tide(concept, now) -> float | None`: `None` for stage 0, else
`0.7 ** (days_since(last_reviewed_at) / INTERVAL_DAYS[stage])`, where days are
fractional. Tide is 1.0 right after a review and exactly 0.7 at
`next_review_at`, so "due" and "tide ≤ 70%" are the same condition.

## Quiz graph

### State (`QuizState`)

`user_id`, `subject_id`, `session_id`, `studied_concept_ids` (concepts already
studied this session), `concept` (current), `batch` (unasked questions),
`question` (current), `answer`, `grade`, `asked_on_concept`, `correct_streak`,
`concepts_done`, `quit`, `generation_calls_on_concept`, `results` (per-concept
summary lines).

### Nodes

| Node | Kind | Does |
|---|---|---|
| `load_learner` | code | loads the subject's concepts; creates the `study_sessions` row |
| `pick_concept` | code | chooses the next concept not yet studied this session: due first, then lowest stage, then oldest `last_reviewed_at` (nulls first); resets per-concept counters |
| `generate_questions` | LLM | batch of 5 for the concept: ≥2 MCQ, ≥1 short answer, difficulties spanning 1–3; previous prompts in context to avoid repeats; rows saved to `questions` |
| `ask_question` | interrupt | picks the next question from the batch (see routing), calls `interrupt(question)`, receives `{attempt_id, answer}` or `{quit: true}` |
| `grade_answer` | code / LLM | MCQ: `grading.grade_mcq` (answer is `1`–`4` or the option text). Short: `tutor.grade(...)` → `Grade` |
| `update_mastery` | code | `mastery.apply_grade`; writes the attempt and the concept update in one transaction |
| `teach` | LLM | ≤120-word explanation aimed at the misconception, printed before the next question |
| `next_concept` | code | records the concept's result line, increments `concepts_done` |
| `summarize` | code | sets `study_sessions.ended_at`; returns per-concept results (grade counts, tide before → after, next review date) |

### Routing (`routing.py`)

`after_grade(state) -> "teach" | "next_question" | "next_concept" | "summarize"`:

1. `quit` → `summarize` (also checked right after `ask_question`, skipping
   grading).
2. grade `incorrect` → `teach`.
3. otherwise → `decide_next(state)`.

`decide_next(state)` (also the outgoing edge of `teach`):

- concept done if `correct_streak >= 2` or `asked_on_concept >= 4`;
- if concept done: `summarize` when `concepts_done + 1 >= 3` or no unstudied
  concepts remain, else `next_concept`;
- else `next_question`.

`correct_streak` resets on partial or incorrect. Question choice within the batch:
after a correct answer, the lowest unasked difficulty above the current one
(falling back to the same difficulty); after partial or incorrect, the same
difficulty (falling back to any). If the batch is empty, route through
`generate_questions` again, at most 2 generation calls per concept; past that,
the concept is done.

### Checkpointer and the CLI

M1 compiles the graph with LangGraph's in-memory checkpointer (`InMemorySaver`);
M2 swaps in `PostgresSaver` with no node changes. The CLI drives the graph:
stream until an interrupt, render the question with Rich (MCQ options numbered
1–4), read input, then resume with `Command(resume={"attempt_id": uuid4(),
"answer": text})`. Typing `q` resumes with `{"quit": true}`.

## LLM boundary (`llm/`)

```python
class Tutor(Protocol):
    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]: ...
    def generate_questions(self, concept: Concept, avoid: list[str]) -> Generated[list[Question]]: ...
    def grade(self, question: Question, answer: str) -> Generated[Grade]: ...
    def teach(self, question: Question, answer: str, grade: Grade) -> Generated[str]: ...
```

`Generated[T]` carries the value, the model that produced it and whether a
backup model was used.

- `ConceptList`: 6–10 items of `{name ≤ 60 chars, description: one sentence}`.
- `QuestionBatch`: 5 `Question`s validated as above (MCQ has exactly 4 options and
  exactly one correct).
- `Grade`: `{grade, explanation, misconception: str | None}`.
- `LangChainTutor` builds `ChatGoogleGenerativeAI` (temperature 0.2 for grading,
  0.7 for generation) and, when `GROQ_API_KEY` is set, a `ChatGroq` backup. It
  tries the providers in order itself rather than using `.with_fallbacks()`, so
  it knows which model answered (for `attempts.model_used` and the backup-model
  notice). Each call uses `.with_structured_output(...)` and
  `.with_retry(stop_after_attempt=3)` (one try plus two retries).
- Prompts live in `llm/prompts/{extract_concepts,generate_questions,grade,teach}.md`.

## Errors

| Class | Trigger | Handling | User sees |
|---|---|---|---|
| `PrimaryUnavailable` | Gemini 429/5xx | fallback to Groq | dim line "Switched to backup model" |
| `OutputInvalid` | Pydantic validation fails | retried twice | if still failing, as below |
| `AllProvidersExhausted` | all providers or retries fail | turn stops; nothing written, stage unchanged | "The tutor couldn't respond — your progress is saved. Try again in a minute." |
| `ConfigError` | missing `GOOGLE_API_KEY`; database unreachable | exit code 2 at startup | one line naming the variable or service |

Graph nodes catch only these named classes; no bare `except Exception`.
If grading a short answer fails with `AllProvidersExhausted`, the same question
is asked again; if generating questions fails, the session ends through
`summarize` with what was completed so far.

## Idempotency

`attempts.id` is the `attempt_id` sent with the answer. `update_mastery` inserts
with `on conflict (id) do nothing` and applies the concept update only if the
insert happened, so a duplicate resume writes one attempt and moves the stage
once.

## Testing

| Layer | What | Needs |
|---|---|---|
| Pure | Leitner table over every (stage 0–5 × due/not due × grade) case; invariant `next_review_at = last_reviewed_at + interval`; tide 1.0 at t=0, 0.7 at the interval, strictly decreasing; every `after_grade` / `decide_next` outcome; MCQ grading by number and by text | nothing |
| Graph | full session through `interrupt`/resume with `FakeTutor` (canned Pydantic objects) and an in-memory repo; quit mid-session; incorrect → teach path; batch exhaustion → regeneration cap | nothing |
| LLM wiring | `LangChainTutor.teach` with `GenericFakeChatModel` | nothing |
| DB | migrations apply once and are idempotent; repository round-trips; duplicate `attempt_id` writes one row and moves the stage once; every repo query is scoped by `user_id` (user B sees nothing of user A's) | Postgres |

DB tests read `TEST_DATABASE_URL` (default
`postgresql://ebb:ebb@localhost:5432/ebb_test`), reset the `public` schema, run
migrations, and are skipped with a clear message if the database is unreachable.

**CI** (`.github/workflows/ci.yml`): on push and pull request, Ubuntu, a
`postgres:18.6-alpine` service with database `ebb_test`, `astral-sh/setup-uv`,
`uv run ruff check`, `uv run pytest`.

## Done when

1. `uv run pytest` passes with no API key and no network (DB tests skipped), and
   passes in full with the compose database running.
2. CI is green on GitHub.
3. With a real `GOOGLE_API_KEY`: `docker compose run --rm ebb new "Computer
   Networks"`, then `… study` completes a session; `… status` shows changed
   stages and tide, and they survive `docker compose down` and `up` (volume kept).
