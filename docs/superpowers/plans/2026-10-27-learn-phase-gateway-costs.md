# Before milestone 5: LLM gateway, cost dashboard and learn phase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shuffle multiple-choice options; route every model call through a role-based gateway that streams and records tokens, TTFT, time and estimated cost; show those records on a dev-only `/costs` page with anomaly rules; and teach each new concept in the web app (a second LangGraph graph, `/teach-me` format, notes + Tavily, Q&A on the same page) before its quiz.

**Architecture:** Phase A changes `ebb/llm/` only: `LangChainTutor._run` becomes a call into `ebb/llm/gateway.py`, which picks a role's chain, streams, and writes one `llm_calls` row per attempt through the `Store`. Phase B is read-only over those rows (`ebb/costs.py`, `/costs` routes, a Next.js page). Phase C adds `ebb/learn/` (graph + service), a `learn` interrupt in the quiz graph for web sessions, learn routes over SSE with text deltas, and the learn cards in the study thread.

**Tech Stack:** Python 3.14, LangGraph 1.2, LangChain core 1.6 (`langchain-groq`, `langchain-mistralai`, new `langchain-openai` for OpenRouter), psycopg 3, FastAPI (`fastapi.sse`), Tavily; Next.js 16.4, React 19, CSS Modules, new `react-markdown` + `remark-gfm`; Vitest, Playwright; Postgres.

**Spec:** [docs/superpowers/specs/2026-10-27-learn-phase-gateway-costs-design.md](../specs/2026-10-27-learn-phase-gateway-costs-design.md). Read it before any task; where this plan is silent, the spec decides. Screens: the design canvas linked in the spec (look only; the spec's *Calls I made* 10 removes its mode chips and "Switches when" column).

## Global Constraints

- Every `Store` method takes `user_id` first and every query filters on it. Every new table has `user_id` referencing `profiles (id) on delete cascade`.
- Graph state holds only JSON values (tests run with `LANGGRAPH_STRICT_MSGPACK=true`). Nodes catch only named exception classes.
- No test calls a real model or Tavily. Backend checks: `cd backend; $env:EBB_REQUIRE_DB=1; $env:LANGGRAPH_STRICT_MSGPACK='true'; uv run pytest -q; uv run ruff check .; uv run ruff format --check .`. Frontend checks: `cd frontend; npm test; npm run typecheck; npm run lint; npm run e2e`.
- Sessions and checkpoints saved by milestone 4 must still resume (a payload without `kind` is a question).
- Styling: CSS Modules over `frontend/styles/tokens.css`; no Tailwind, no chart library. Markdown: `react-markdown` + `remark-gfm`, raw HTML never rendered.
- Settings (exact names and defaults): `EBB_TEACH_MODEL=groq:openai/gpt-oss-120b`, `EBB_QUIZ_MODEL=groq:openai/gpt-oss-120b`, `EBB_EVAL_WRITER_MODEL=mistral:mistral-medium-latest`, `EBB_TEACH_BACKUP` / `EBB_QUIZ_BACKUP` unset, `OPENROUTER_API_KEY` unset, `EBB_SHOW_COSTS=true`. `EBB_PRIMARY_MODEL` / `EBB_FALLBACK_MODEL`, when set, are the defaults of the teach/quiz main model and of both backups. Model spec format `provider:model`, provider ∈ `groq | openrouter | mistral`.
- Roles: `teach` = lesson, learn answers, re-teach; `quiz` = write/rewrite/web questions, grade, regrade, extract/extend concepts, concepts from notes; `eval_writer` = eval cases.
- Endpoint names (exact): `lesson`, `learn_answer`, `write_questions`, `write_web_questions`, `rewrite_questions`, `grade`, `regrade`, `reteach`, `extract_concepts`, `extend_concepts`, `concepts_from_notes`, `write_eval_case`.
- Attempt outcomes (exact): `ok`, `invalid`, `error`, `refused`.
- Anomaly thresholds: tokens > 3× median of the endpoint's previous 50 `ok` calls (needs ≥ 10); TTFT > 3× median **and** > 2000 ms; total > 3× median **and** > 5000 ms; a day's tokens > 2× mean of the 7 days before (needs ≥ 3 days with calls).
- Exact copy:
  - `"Lesson · quiz opens when you press Ready"`
  - `"Your notes only"`, `"General knowledge · not from your notes"`, `"Not in your notes"`
  - `"Answer this for me"`, `"Suggested next"`, `"Ask your own question about {concept}"`
  - `"Ready to be quizzed?"`, `"Ebb starts with multiple choice. Questions you asked here do not change your tide or your review schedule."`, `"I'm ready, quiz me"`
  - `"Read the lesson again"`, `"learning · not quizzed yet"`
  - `"Web search unavailable — this lesson uses your notes only."`
  - `"No model calls yet in this range."`, `"price unknown"`, `"not reported"`, `"Real spend: $0.00 on free tiers"` (only when every model in the range has `free_tier`)
  - `"OPENROUTER_API_KEY is not set — {setting} uses OpenRouter"` (and the same shape for `GROQ_API_KEY` / `MISTRAL_API_KEY`)
  - Step labels: `"Writing the lesson…"`, `"Searching the web…"` (existing), `"Answering…"`
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A provider that reports no usage or whose structured reply arrives in one chunk**: the row stores null tokens (shown `"not reported"`) and the TTFT of that one chunk, never a guess — test in Task 4.
2. **A refresh or closed tab while a lesson is streaming**: the server finishes the lesson, stores it once, and the next open shows the stored copy with no second model call — test in Task 11.
3. **Ready pressed twice, or Ready after the quiz session was ended elsewhere**: the concept is marked learned once; the second press returns the current quiz turn or a clear "session ended" — tests in Tasks 11 and 12.
4. **Lesson markdown containing `<script>`, raw HTML or a `javascript:` link**: shown as text, never executed or linked — test in Task 14.
5. **A Costs range with zero or one call, or a model with no price**: no division error or `NaN`; percentiles show "—", cost shows `"price unknown"` — test in Task 6.

---

## Phase A — Shuffle, roles, gateway, records

### Task 1: Shuffle multiple-choice options

**Files:** Modify `backend/src/ebb/llm/tutor.py` (`generate_questions.to_questions`), `backend/tests/fakes.py`, `backend/tests/e2e_server.py`, `frontend/e2e/session.spec.ts`, any CLI test that assumes option 1 is correct; Test `backend/tests/test_tutor.py`

**Interfaces:**
- Produces: `LangChainTutor(providers, attempts=3, rng: random.Random | None = None)`; MCQ options are shuffled with `self.rng.shuffle` after validation, before `Question` is built. `FakeTutor` gains the same `rng` parameter and shuffles its MCQ options when one is given (default: no shuffle, so existing graph tests keep their order).

- [ ] **Step 1: Failing tests:** `test_mcq_options_are_shuffled_with_the_given_rng` (seeded `random.Random(3)`, a fake model returning options with option 1 correct → the saved order equals `rng.shuffle` of the list); `test_correct_option_lands_in_every_position` (200 batches, unseeded → each of positions 0–3 holds the correct option at least once); `test_short_answers_untouched`.
- [ ] **Step 2:** `uv run pytest tests/test_tutor.py -q` → FAIL.
- [ ] **Step 3:** Implement. Make the e2e fake shuffle with a fixed seed and update `session.spec.ts` to choose the option the fake marks correct (the fake's correct text, not position 1).
- [ ] **Step 4:** Backend suite and `npm run e2e` → PASS.
- [ ] **Step 5:** Commit `fix: shuffle multiple-choice options so the answer isn't always first`.

### Task 2: Roles and providers from settings

**Files:** Create `backend/src/ebb/llm/providers.py`; Modify `backend/src/ebb/config.py`, `backend/pyproject.toml` (add `langchain-openai`), `.env.example`, `backend/src/ebb/llm/tutor.py` (`from_settings`, `writer`, remove `_groq_provider`/`_mistral_provider`); Test `backend/tests/test_config.py`, `backend/tests/test_providers.py` (new), `backend/tests/test_env_example.py`

**Interfaces:**
- Produces:
  - `config.ModelSpec(provider: Literal["groq","openrouter","mistral"], model: str)`; `config.parse_model(value: str, setting: str) -> ModelSpec` (raises `ConfigError` naming the setting on a bad value).
  - `Settings` fields from Global Constraints plus `Settings.role_models() -> dict[Role, tuple[ModelSpec, ModelSpec | None]]` (main, backup), applying the `EBB_PRIMARY_MODEL`/`EBB_FALLBACK_MODEL` defaults; today's Mistral fallback is the backup when `MISTRAL_API_KEY` is set and no `*_BACKUP` is given.
  - `providers.Role = Literal["teach","quiz","eval_writer"]`; `providers.chat_models(spec: ModelSpec, settings: Settings, setting: str) -> Provider` building `precise` (0.2) and `creative` (0.7) streaming chat models (`ChatGroq`, `ChatMistralAI`, `ChatOpenAI(base_url="https://openrouter.ai/api/v1")`), with usage on streams enabled where the client needs a flag; raises `ConfigError` with the exact missing-key copy.
  - `Provider` moves to `providers.py` and gains `provider: str` (`"groq"` etc.); `name` stays the model name.
  - `LangChainTutor.from_settings(settings, fallback=True)` builds `{role: [main, backup?]}`; `LangChainTutor.writer(settings)` builds only `eval_writer` and raises `ConfigError("EBB_EVAL_WRITER_MODEL must differ from EBB_QUIZ_MODEL (the grader)")` when they are equal.

- [ ] **Step 1: Failing tests:** `test_parse_model_splits_provider_and_model` (`"openrouter:qwen/x"`), `test_parse_model_rejects_unknown_provider`, `test_legacy_primary_model_is_the_default`, `test_missing_openrouter_key_names_the_setting` (exact copy), `test_eval_writer_equal_to_grader_is_refused`, `test_env_example_lists_new_settings`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; `uv add langchain-openai`. **Step 4:** Suite → PASS (existing tutor tests build providers through the new factory or construct `Provider` directly).
- [ ] **Step 5:** Commit `feat: role-based model settings with OpenRouter as a provider`.

### Task 3: Migration 005 and call records in the store

**Files:** Create `backend/src/ebb/db/migrations/005_gateway_learn.sql`; Modify `backend/src/ebb/domain.py`, `backend/src/ebb/store.py`, `backend/src/ebb/db/repo.py`, `backend/tests/fakes.py`; Test `backend/tests/test_db.py`, `backend/tests/test_fakes.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- Produces:
  - Tables (spec *Schema*): `llm_calls` (id uuid pk, call_id uuid, request_id uuid null, user_id, at timestamptz, role, endpoint, provider, model, attempt int, outcome, input_tokens int null, output_tokens int null, reasoning_tokens int null, ttft_ms int null, total_ms int, cost_usd numeric(12,8) null, error_kind text null; indexes `(user_id, at)`, `(user_id, endpoint, at)`); `concept_lessons` (user_id, concept_id pk references concepts on delete cascade, markdown, citations jsonb, web_pages jsonb, suggestions jsonb, source_summary jsonb, model, written_at, learned_at null); `lesson_exchanges` (id pk, user_id, concept_id, position int, kind `check|suggested|typed`, question, answer, citations jsonb, suggestions jsonb, asked_at; unique `(concept_id, position)`).
  - `domain.LlmCall` (frozen dataclass, the columns above).
  - `Store.record_llm_call(user_id, call: LlmCall) -> None`; `Store.llm_calls(user_id: UUID | None, since: datetime, until: datetime) -> list[LlmCall]` (`user_id=None` = all tenants, for the dev page only; ordered by `at`). Implemented in `repo.py` and `MemoryStore`.

- [ ] **Step 1: Failing tests:** `test_migration_005_applies_on_004` ; `test_record_and_list_llm_calls_round_trip` (db and memory, nullable fields stay `None`); `test_llm_calls_filters_by_user_and_range`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Suite → PASS.
- [ ] **Step 5:** Commit `feat: migration 005 — llm_calls, concept_lessons, lesson_exchanges`.

### Task 4: The gateway — stream, time, record every attempt

**Files:** Create `backend/src/ebb/llm/gateway.py`, `backend/src/ebb/llm/prices.py`, `backend/src/ebb/llm/request.py`; Modify `backend/src/ebb/llm/tutor.py` (`_run` delegates; each method names its role and endpoint), `backend/src/ebb/api/services.py`, `backend/src/ebb/cli.py`, `backend/src/ebb/evals.py` (construction only); Test `backend/tests/test_gateway.py` (new), `backend/tests/test_tutor.py`

**Interfaces:**
- Consumes: Task 2 `Provider`, `Role`; Task 3 `Store.record_llm_call`, `LlmCall`.
- Produces:
  - `request.request_scope(user_id: UUID) -> ContextManager[UUID]` sets a context variable with a fresh request id and the user; `request.current() -> tuple[UUID | None, UUID | None]`. Nested scopes keep the outer id.
  - `prices.PRICES: dict[str, Price]` keyed by model name, `Price(input_per_m: Decimal, output_per_m: Decimal, source: str, as_of: date, free_tier: bool)`; `prices.cost(model, input_tokens, output_tokens) -> Decimal | None` (None when the model or either count is unknown). Fill from each provider's pricing page on the day of the task, with the URL as `source`.
  - `gateway.Recorder` protocol `record(call: LlmCall) -> None`; `gateway.StoreRecorder(store)` writes through `Store.record_llm_call` and logs (warning) and swallows `psycopg.Error`; `gateway.NullRecorder`.
  - `gateway.Gateway(chains: dict[Role, list[Provider]], recorder: Recorder, attempts: int = 3, clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = utc_now)`; `Gateway.call[T](role, endpoint, schema: type[BaseModel] | None, prompt: str, creative: bool, parse: Callable[[Any], T], on_text: Callable[[str], None] | None = None) -> Generated[T]` — same error semantics as today's `_run` (retry on `_retryable`, `TutorRefused` ends the call, `bad_key`/`no_model` → `ConfigError`, backup after the main is exhausted, `fell_back=index > 0`). Streams with `.stream(...)`: TTFT = clock at first chunk − start; total = clock at end − start; usage from the stream's `usage_metadata` (input, output, `output_token_details.reasoning`) or `None`; for `schema=None`, each chunk's text goes to `on_text`. One `LlmCall` per attempt, all sharing a `call_id`, with the request id/user from `request.current()`, outcome `ok | invalid | error | refused`, `cost_usd` from `prices.cost`.
  - `LangChainTutor(chains, recorder=NullRecorder(), attempts=3, rng=None)`; `from_settings(settings, fallback=True, recorder=NullRecorder())`. The API and CLI pass `StoreRecorder(store)`.

- [ ] **Step 1: Failing tests** (fake streaming chat models from `langchain_core.language_models.fake_chat_models`, a fake clock): `test_records_tokens_ttft_and_total_for_an_ok_call` (exact ms from the fake clock, tokens from the fake usage, `cost_usd` from a test price); `test_retry_and_fallback_each_write_a_row_with_one_call_id` (outcomes `invalid, invalid, invalid, ok`, last row `fell_back` via provider index); `test_refusal_records_refused_and_stops`; `test_missing_usage_records_none` (Review Focus 1); `test_single_chunk_reply_ttft_equals_that_chunk` (Review Focus 1); `test_unknown_price_records_null_cost`; `test_recorder_failure_does_not_fail_the_call` (recorder raising `psycopg.OperationalError`); `test_on_text_receives_streamed_text_for_plain_calls`; `test_request_scope_tags_rows`; `test_each_tutor_method_uses_its_role_and_endpoint` (parametrised over the Global Constraints table).
- [ ] **Step 2:** `uv run pytest tests/test_gateway.py tests/test_tutor.py -q` → FAIL.
- [ ] **Step 3:** Implement. Structured replies: stream the chain from `_chain(...)`; the last chunk is the parsed value (keep `include_raw=True` handling); the builder may switch to accumulating raw chunks and parsing at the end if partial parsing misbehaves — the tests decide.
- [ ] **Step 4:** Full backend suite → PASS.
- [ ] **Step 5:** Commit `feat: LLM gateway streams every call and records tokens, TTFT, time and cost`.

### Task 5: Request ids where users act

**Files:** Modify `backend/src/ebb/study.py` (each `_stream` run), `backend/src/ebb/api/drafts.py` (each file), `backend/src/ebb/subjects.py`, `backend/src/ebb/cli.py` (each command step), `backend/src/ebb/evals.py` (each case); Test `backend/tests/test_study.py`, `backend/tests/test_drafts.py`

**Interfaces:**
- Consumes: Task 4 `request.request_scope`.
- Produces: every quiz turn, uploaded file, CLI step and eval case runs inside one `request_scope(user_id)`. Calls inside a worker thread receive the scope explicitly (copy the context with `contextvars.copy_context().run` where `drafts.py` spawns threads).

- [ ] **Step 1: Failing tests:** `test_one_quiz_turn_shares_one_request_id` (FakeTutor wrapped to record `request.current()`; grade + teach + generate in one reply → one id; next reply → a new id); `test_each_uploaded_file_has_its_own_request_id`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Suite → PASS.
- [ ] **Step 5:** Commit `feat: group model calls by the user action that caused them`.

## Phase B — Costs

### Task 6: Cost aggregation and anomaly rules (pure)

**Files:** Create `backend/src/ebb/costs.py`; Test `backend/tests/test_costs.py`

**Interfaces:**
- Consumes: Task 3 `LlmCall`; Task 4 `prices`.
- Produces (all pure over `Sequence[LlmCall]`):
  - `summary(calls) -> Summary` (est. cost total or None if any `ok` call lacks a price — then also `unpriced_calls: int`; tokens in/out (None when none reported); calls (logical, by `call_id`), retried, fell back; TTFT p50/p95 and total p50/p95 over `ok` rows (None with < 1 value); fallback rate; retry rate; `all_free_tier: bool` from `prices.PRICES`).
  - `by_feature(calls)`, `by_endpoint(calls)`, `by_tenant(calls)`, `by_model(calls)` (calls, TTFT p50/p95, total p50/p95, output tokens per second, fallbacks), `per_day(calls, tz) -> list[DayTokens]`, `role_health(calls, chains) -> list[RoleHealth]` (failed-attempt share, p95 total).
  - `anomalies(calls, history: Sequence[LlmCall], tz) -> list[Anomaly]` with `Anomaly(kind: Literal["tokens","ttft","total","day"], call_id: UUID | None, day: date | None, endpoint: str | None, ratio: float, message: str)`; messages in words, e.g. `"Grade answer at 14:02 used 4.1 times the input tokens a grading call usually uses"`. Thresholds from Global Constraints; `history` is the calls before the range (the API reads 50 per endpoint and 7 days back).
  - `failures(calls) -> list[LlmCall]` (`error`, `refused`, and calls whose every attempt failed).
  - Feature grouping: lessons and re-teaching = `lesson`, `reteach`; question writing = `write_questions`, `write_web_questions`, `rewrite_questions`; grading and disputes = `grade`, `regrade`; learn-phase questions = `learn_answer`; concept extraction = `extract_concepts`, `extend_concepts`, `concepts_from_notes`; evals = `write_eval_case`.

- [ ] **Step 1: Failing tests:** `test_summary_of_no_calls_is_empty_not_nan`, `test_summary_of_one_call` (p50 = p95 = that value) (Review Focus 5); `test_unpriced_model_makes_cost_unknown` (Review Focus 5); `test_token_anomaly_needs_ten_previous_calls`; `test_token_anomaly_over_three_times_median`; `test_ttft_anomaly_needs_both_ratio_and_two_seconds`; `test_total_anomaly_needs_both_ratio_and_five_seconds`; `test_day_anomaly_over_twice_the_seven_day_mean`; `test_failures_listed_not_anomalies`; `test_by_feature_groups`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: cost summaries and anomaly rules over call records`.

### Task 7: Costs API

**Files:** Create `backend/src/ebb/api/routes_costs.py`; Modify `backend/src/ebb/api/app.py`, `backend/src/ebb/api/schemas.py` (`Health` gains `costs: bool`), `backend/src/ebb/api/services.py`; Test `backend/tests/test_api_costs.py` (new), `backend/tests/test_api_health.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 6.
- Produces: `GET /costs/summary?range=24h|7d|30d&tenant=&endpoint=&model=` → `{summary, by_feature, per_day, roles, by_model, by_endpoint, by_tenant, anomalies, failures}`; `GET /costs/calls?…&limit=50` → newest attempts first, each with `call_id`, `request_id` and an `anomaly` message or null; `GET /costs/prices` → `[{model, input_per_m, output_per_m, source, as_of, free_tier}]`. All 404 when `EBB_SHOW_COSTS` is false. `/health` reports `costs: true|false`.

- [ ] **Step 1: Failing tests:** `test_costs_404_when_hidden`; `test_summary_filters_by_endpoint_and_model`; `test_calls_newest_first_with_anomaly_message`; `test_health_reports_costs_flag`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: /costs API for the dev cost dashboard`.

### Task 8: Costs page

**Files:** Create `frontend/app/costs/page.tsx`, `frontend/components/costs/*` (+ `.module.css`, tests); Modify `frontend/components/Header.tsx` (+ test), `frontend/lib/types.ts`; Test `frontend/components/costs/*.test.tsx`, `frontend/components/Header.test.tsx`

**Interfaces:**
- Consumes: Task 7 routes and `Health.costs`.
- Produces: `/costs` with the spec's sections in order (filters, anomalies banner, summary cards, cost by feature, tokens per day, gateway roles, speed by model, by endpoint, by tenant, recent calls with a request's attempts expandable, where prices come from). Loading/error/narrow layout copied from `app/progress/[id]/page.tsx`. Header shows **Costs** with a "dev only" tag only when `/health` says `costs: true`. Null numbers render `"not reported"`, null cost `"price unknown"`, empty range `"No model calls yet in this range."`.

- [ ] **Step 1: Failing tests:** `CostsPage renders every section from a fixture`; `shows "not reported" and "price unknown" for nulls`; `empty range shows the empty copy`; `Header hides Costs when health.costs is false`.
- [ ] **Step 2:** `npm test` → FAIL. **Step 3:** Implement. **Step 4:** `npm test; npm run typecheck; npm run lint` → PASS.
- [ ] **Step 5:** Commit `feat: dev-only Costs page`.

## Phase C — Learn phase

### Task 9: Lesson and answer calls

**Files:** Create `backend/src/ebb/llm/prompts/learn_lesson.md`, `backend/src/ebb/llm/prompts/learn_answer.md`, `backend/src/ebb/llm/trailer.py`; Modify `backend/src/ebb/domain.py`, `backend/src/ebb/llm/tutor.py`, `backend/src/ebb/llm/quota.py`, `backend/tests/fakes.py`; Test `backend/tests/test_trailer.py` (new), `backend/tests/test_tutor.py`, `backend/tests/test_quota.py`

**Interfaces:**
- Produces:
  - `domain.LearnText(markdown: str, claims: tuple[Claim, ...], suggestions: tuple[str, ...], checks: tuple[str, ...])` (`checks` = the check-yourself questions, lesson only).
  - `Tutor.write_lesson(concept: Concept, subject: str, sources: Sequence[Source], on_text: Callable[[str], None] | None = None, failures: Sequence[str] = (), previous: LearnText | None = None) -> Generated[LearnText]` (role `teach`, endpoint `lesson`); `Tutor.answer(concept: Concept, lesson: str, history: Sequence[tuple[str, str]], question: str, sources: Sequence[Source], on_text=None, failures=(), previous=None) -> Generated[LearnText]` (endpoint `learn_answer`). A citation retry (`failures` given) re-cites only: it keeps `previous.markdown` and returns new claims, no streaming.
  - `trailer.split(text: str) -> tuple[str, dict]` and `trailer.Streamer(on_text)` — the model writes markdown, then a line `<<<EBB` and a JSON object `{"citations":[{"source","quote"}], "suggestions":[…], "checks":[…]}` then `EBB>>>`; `Streamer` forwards text to `on_text` until it sees the start of the marker (holding back a partial `<<<` across chunks). Missing or invalid trailer → `OutputParserException` (retried by the gateway).
  - `learn_lesson.md`: the `/teach-me` blocks and scaling rule from the spec, title = concept name, "do not answer the check-yourself questions", aim 600–1,200 words for a deep concept, fenced sources, cite with `[label]` sources only in the trailer. `learn_answer.md`: answer the one question from the lesson and sources, steer off-topic questions back briefly, 2–3 suggestions.
  - `CappedTutor.write_lesson` / `answer` take one daily call each. `FakeTutor` implements both deterministically (markdown with a `#` title, one table, three checks, two suggestions; streams it in 3 chunks to `on_text`; cites the first source when given).

- [ ] **Step 1: Failing tests:** `test_streamer_never_forwards_the_trailer` (marker split across chunks); `test_split_reads_citations_suggestions_checks`; `test_missing_trailer_is_a_parse_error`; `test_write_lesson_streams_markdown_and_returns_claims` (fake model); `test_citation_retry_keeps_markdown`; `test_capped_tutor_counts_learn_calls`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: lesson and learn-answer calls in the /teach-me format`.

### Task 10: Lessons and exchanges in the store

**Files:** Modify `backend/src/ebb/domain.py`, `backend/src/ebb/store.py`, `backend/src/ebb/db/repo.py`, `backend/tests/fakes.py`; Test `backend/tests/test_db.py`, `backend/tests/test_fakes.py`

**Interfaces:**
- Consumes: Task 3 tables; Task 9 `LearnText`.
- Produces: `domain.StoredLesson(concept_id, markdown, citations: tuple[Citation, ...], web_pages: tuple[dict, ...], suggestions, checks, source_summary: dict, model, written_at, learned_at)`; `domain.Exchange(position, kind, question, answer, citations, suggestions, asked_at)`. Store: `get_lesson(user_id, concept_id) -> StoredLesson | None`; `save_lesson(user_id, lesson: StoredLesson) -> None` (insert; a second save for the same concept is ignored — first writer wins); `mark_learned(user_id, concept_id, at) -> bool` (False if already learned); `add_exchange(user_id, concept_id, exchange) -> None`; `exchanges(user_id, concept_id) -> list[Exchange]`; `is_learned(user_id, concept_id) -> bool`.

- [ ] **Step 1: Failing tests:** `test_save_lesson_first_writer_wins`; `test_mark_learned_once` (second call False); `test_exchanges_in_order`; `test_lesson_deleted_with_concept`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: store lessons and learn-phase exchanges`.

### Task 11: The learn graph and its service

**Files:** Create `backend/src/ebb/learn/__init__.py`, `backend/src/ebb/learn/graph.py`, `backend/src/ebb/learn/service.py`; Test `backend/tests/test_learn.py` (new)

**Interfaces:**
- Consumes: Tasks 9, 10; existing `store.search_chunks`, `citations.note_sources`, `citations.web_sources`, `citations.verify`, `retry.fix_citations`, `WebSearch`.
- Produces:
  - `graph.build_learn_graph(tutor, store, checkpointer, now=utc_now, web: WebSearch | None = None) -> CompiledStateGraph` with nodes `gather_sources → write_lesson → wait`, `wait --ask--> gather_sources → answer → wait`, `wait --ready--> mark_learned → END`. Thread id `learn:{user_id}:{concept_id}`. `write_lesson` returns the stored lesson without a model call when `store.get_lesson` has one. Text deltas are written with `get_stream_writer()({"text": delta})`; step labels `"Searching the web…"`, `"Writing the lesson…"`, `"Answering…"` as `{"step", "label"}`. A failed search adds notice `"Web search unavailable — this lesson uses your notes only."`. Verified claims via `fix_citations`; the lesson (with `source_summary`: notes pages or `"Your notes only"` / `"General knowledge · not from your notes"`, web page count) is saved before `wait`; each answer is saved as an `Exchange` before `wait`.
  - `wait` interrupts with `{"kind": "learn", "concept_id", "concept", "lesson": StoredLesson as JSON, "exchanges": [...], "suggestions": [...], "notices": [...]}`; replies `{"ask": str, "kind": "check"|"suggested"|"typed", "ask_id": str}` (an `ask_id` already answered is not re-asked) or `{"ready": true}`.
  - `service.LearnSessions(store, graph, saver, now)` with `start(user_id, concept_id) -> Iterator[LearnEvent]` (start or resume; raises `LookupError` for another user's concept) and `reply(user_id, concept_id, reply) -> Iterator[LearnEvent]`, where `LearnEvent = Step | TextDelta(text) | Discard | LearnTurn(payload) | Learned(concept_id)`. A `TutorUnavailable` after any `TextDelta` yields `Discard` then raises. After `Learned`, the checkpoint thread is deleted. Like `StudySessions._stream`, a reader going away finishes the run (Review Focus 2).

- [ ] **Step 1: Failing tests** (FakeTutor, MemoryStore, `InMemorySaver`, fake `WebSearch`): `test_start_streams_text_then_waits_with_the_lesson`; `test_second_start_uses_stored_lesson_without_a_model_call` (FakeTutor call count); `test_ask_answers_and_saves_an_exchange`; `test_same_ask_id_is_not_answered_twice`; `test_ready_marks_learned_once_and_deletes_the_thread` (Review Focus 3); `test_no_web_key_labels_notes_only`; `test_failed_search_is_a_notice`; `test_failure_after_text_yields_discard_then_raises`; `test_reader_gone_mid_lesson_still_stores_it` (Review Focus 2); `test_learning_never_changes_stage_or_tide`.
- [ ] **Step 2:** `uv run pytest tests/test_learn.py -q` → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: learn graph — lesson, questions and ready, with streamed text`.

### Task 12: The quiz pauses for a lesson in web sessions

**Files:** Modify `backend/src/ebb/graph/state.py` (`learn_first: bool`, default `False`), `backend/src/ebb/graph/nodes.py` (new node `offer_lesson`), `backend/src/ebb/graph/build.py`, `backend/src/ebb/routing.py` (`after_pick`), `backend/src/ebb/study.py` (`start(..., learn_first=False)`, `_stopped_on`, `_next`), `backend/src/ebb/cli.py`; Test `backend/tests/test_graph.py`, `backend/tests/test_study.py`, `backend/tests/test_resume.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: Task 10 `Store.is_learned`.
- Produces: `after_pick` returns `"offer_lesson"` when `state["learn_first"]` and the picked concept has `stage == 0`, no answers, and is not learned. `offer_lesson` interrupts with `{"kind": "learn", "concept": name, "concept_id": id, "number": 0}`; reply `{"learned": true}` (accepted only if `store.is_learned`, otherwise the same payload is returned) → `retrieve_notes`; reply `{"skip": true}` (the CLI) → `retrieve_notes` without marking. `StudySessions.start(..., learn_first)`: the API passes `True`, the CLI `False`. The CLI's turn loop answers a `kind == "learn"` payload with `{"skip": true}` silently. `_stopped_on` reports `(concept, 1)` for a learn payload. The first question after `learned` is the batch's easiest MCQ when it has one (`choose_question(..., prefer_mcq=True)`).

- [ ] **Step 1: Failing tests:** `test_web_session_pauses_on_a_new_concept`; `test_cli_session_never_pauses`; `test_concept_with_answers_is_not_offered_a_lesson`; `test_learned_reply_continues_to_questions_mcq_first`; `test_learned_reply_without_lesson_learned_repeats_the_offer`; `test_cli_resuming_a_paused_web_session_skips_the_lesson` (Review Focus 3, ended-elsewhere path: `reply` on an ended thread raises `SessionNotFound`); `test_milestone4_checkpoint_without_learn_first_resumes`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Suite → PASS.
- [ ] **Step 5:** Commit `feat: web quiz sessions pause on a new concept for its lesson`.

### Task 13: Learn API

**Files:** Create `backend/src/ebb/api/routes_learn.py`; Modify `backend/src/ebb/api/app.py`, `backend/src/ebb/api/services.py` (build the learn graph and `LearnSessions`), `backend/src/ebb/api/routes_study.py` (start passes `learn_first=True`; reply accepts `{"learned": true}`), `backend/src/ebb/api/schemas.py`; Test `backend/tests/test_api_learn.py` (new), `backend/tests/test_api_study.py`

**Interfaces:**
- Consumes: Tasks 11, 12.
- Produces: `POST /concepts/{concept_id}/learn` and `POST /concepts/{concept_id}/learn/reply` (body `{ask, kind, ask_id}` or `{ready: true}`), both SSE with events `step {label}`, `text {delta}`, `discard {}`, `learn {payload}`, `learned {concept_id}`, `error {notice}|{error}` (existing shapes). `GET /concepts/{concept_id}/lesson` → `{lesson, exchanges}` or 404 (read-only; no model call). The quiz SSE's `turn` event carries the learn payload unchanged (`kind: "learn"`).

- [ ] **Step 1: Failing tests:** `test_learn_stream_event_order` (`step`, `text`…, `learn`); `test_ready_emits_learned`; `test_get_lesson_makes_no_model_call`; `test_discard_then_error_on_mid_stream_failure`; `test_quiz_start_returns_a_learn_turn_for_a_new_concept`; `test_quiz_reply_learned_continues`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Suite → PASS.
- [ ] **Step 5:** Commit `feat: learn-phase API with streamed lesson text`.

### Task 14: Learn phase in the study thread

**Files:** Create `frontend/components/study/LearnLesson.tsx`, `LearnExchange.tsx`, `ReadyCard.tsx`, `Markdown.tsx` (+ `.module.css`, tests), `frontend/components/study/useLearn.ts`; Modify `frontend/package.json` (`react-markdown`, `remark-gfm`), `frontend/lib/types.ts`, `frontend/lib/session.ts` (`Card` gains `learn`, `exchange`, `ready`), `frontend/components/study/Thread.tsx`, `Composer.tsx`, `useStudy.ts`, `SubjectColumn.tsx`, `MemoryPanel.tsx`; Test the new components' tests, `Thread.test.tsx`, `session.test.ts`

**Interfaces:**
- Consumes: Task 13 routes and events; quiz `turn` with `kind: "learn"`.
- Produces: when a quiz turn has `kind: "learn"`, `useStudy` hands control to `useLearn(conceptId)`, which streams the lesson into a `learn` card (live text from `text` deltas; `discard` clears it and shows the existing `ErrorCard` with **Retry**), then renders exchanges, `"Suggested next"` chips, and the Ready card. **Answer this for me** / a chip / the composer post an ask (`ask_id` = a fresh UUID, reused on transport retry). The composer is enabled during the learn phase with placeholder `"Ask your own question about {concept}"`. **I'm ready, quiz me** posts `{ready: true}`, then on `learned` replies `{learned: true}` to the quiz session and continues as today. Cards persist in the tab's session storage like quiz cards. `Markdown` renders with `react-markdown` + `remark-gfm`, `skipHtml`, and a `urlTransform` that drops non-`http(s)` links. The left column lists **Read the lesson again** for learned concepts (opens the stored lesson + exchanges, read-only, via `GET /concepts/{id}/lesson`). The memory panel shows `"learning · not quizzed yet"` for the concept in its learn phase. Exact copy from Global Constraints.

- [ ] **Step 1: Failing tests:** `Markdown renders a table`; `Markdown shows <script> and raw HTML as text and drops javascript: links` (Review Focus 4); `LearnLesson streams deltas then shows chips`; `discard clears text and shows Retry`; `Answer this for me posts a check ask`; `Ready posts ready then learned to the quiz`; `composer placeholder names the concept during learn`; `Read the lesson again shows stored exchanges`.
- [ ] **Step 2:** `npm test` → FAIL. **Step 3:** Implement; `npm install react-markdown remark-gfm`. **Step 4:** `npm test; npm run typecheck; npm run lint` → PASS.
- [ ] **Step 5:** Commit `feat: learn phase in the study workspace`.

### Task 15: End to end, docs, follow-ups

**Files:** Modify `backend/tests/e2e_server.py`, `frontend/e2e/session.spec.ts` (or new `learn.spec.ts`), `README.md`, `docs/HANDOFF.md`, `.env.example`, `docs/superpowers/m4-followups.md`; Create `docs/superpowers/pre-m5-rulings.md` (decisions taken during the build, in `m4-rulings.md`'s format)

- [ ] **Step 1:** e2e: topic onboarding → the first concept opens its lesson → **Answer this for me** on a check → a suggestion chip → **I'm ready, quiz me** → the first question is multiple choice → answer it → **Costs** shows the run's calls. Run `npm run e2e` → PASS.
- [ ] **Step 2:** README: new settings, OpenRouter, the Costs page, the learn phase, backup as `ebb_pre005` before the first start. HANDOFF: state after this work and what milestone 5 starts from. Mark the follow-ups this work closed (option order, logging) and keep the deferred ones (switching on slowness, token-saving tweaks).
- [ ] **Step 3:** Full backend and frontend checks (Global Constraints) → PASS.
- [ ] **Step 4:** Commit `docs: learn phase, gateway and Costs page; rulings`.
