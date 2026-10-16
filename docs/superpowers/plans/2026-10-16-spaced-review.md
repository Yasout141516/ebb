# Milestone 2: Spaced Review and Resumable Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ebb study` reviews faded concepts first, runs until you quit, grows a subject when everything is learned, never repeats an answered question, resumes after a closed terminal, shows dates in your time zone, and is hardened against cross-user rows, lost updates and stale feedback.

**Architecture:** Builds on milestone 1 (`backend/src/ebb`). Pure decisions stay in `routing.py` / `mastery.py`; persistence grows in the `Store` protocol (`PgStore`, `MemoryStore`); one new graph node (`extend_subject`); LangGraph's `PostgresSaver` replaces `InMemorySaver` in the CLI so a session can be resumed from a new process; a `002` SQL migration adds database-enforced ownership and session activity columns.

**Tech Stack:** as milestone 1 (Python 3.14, uv, LangGraph 1.2, psycopg 3.3, Postgres 18.6, Typer + Rich, pytest) plus `langgraph-checkpoint-postgres` 3.2, `tzdata`, `tzlocal`.

**Spec:** `docs/superpowers/specs/2026-10-16-spaced-review-design.md` (approved). Milestone 1 spec for everything unchanged: `docs/superpowers/specs/2026-10-15-core-loop-design.md`.

## Global Constraints

- New runtime dependencies, exactly: `langgraph-checkpoint-postgres>=3.2.0,<4`, `tzdata>=2026.5`, `tzlocal>=5.4.4,<6`. No others.
- Leitner rules, intervals `{1: 1, 2: 3, 3: 7, 4: 16, 5: 35}`, the tide formula and "promote only when due" (`mastery.py`) do not change. "Due" means `next_review_at <= now` (precise time).
- Every `Store` method takes `user_id` first; every SQL statement filters on it (E1).
- Graph state holds only JSON-style values; tests and CI run with `LANGGRAPH_STRICT_MSGPACK=true`. Nothing before `interrupt()` in `ask_question` may have side effects.
- No bare `except Exception`. Escape all dynamic text printed through Rich markup (`rich.markup.escape`), as milestone 1 does.
- Migrations must keep existing data: the owner's database already holds a milestone 1 subject with progress.
- User-facing copy, exactly:
  - `Welcome back — {n} ideas are ebbing. Reviewing those first.` (n = due reviews; `1 idea is ebbing` when n = 1)
  - `Nothing is due. Let's learn something new.`
  - `You've learned every concept here. Finding more advanced ones…`
  - `You stopped on {concept}, question {number}, {ago}. Resume? [Y/n]` where `{ago}` is `a moment ago` (< 1 min), `{m} minutes ago` / `1 minute ago` (< 60 min), `{h} hours ago` / `1 hour ago` (otherwise)
  - `That session is open in another terminal.`
  - `You're all caught up — next review {day}.` (`{day}` formatted as below; `You're all caught up.` if there is no next review)
  - `EBB_TIMEZONE '{name}' is not a known time zone`
- Dates shown to the user use `"%a %d %b"` in the resolved time zone (e.g. `Sat 17 Oct`).
- Time windows: resumable for 24 hours after `last_active_at`; checkpoints pruned for sessions ended more than 7 days ago; the avoid list holds at most 40 prompts.
- Run commands from the repository root. Test command: `EBB_REQUIRE_DB=1 LANGGRAPH_STRICT_MSGPACK=true uv run --directory backend pytest -q` (the compose `db` must be up: `docker compose up -d --wait`). Lint: `uv run --directory backend ruff check .` and `uv run --directory backend ruff format --check .`.
- Commit messages end with a blank line and a `Co-Authored-By:` trailer naming the model that wrote the commit.

## Review Focus

1. **Answering the resume prompt with something unexpected** (`maybe`, `N`, blank, Ctrl+D): blank and `y`/`yes` resume, `n`/`no` decline (any case), anything else re-asks; end of input or Ctrl+C at the prompt exits with code 0 and leaves the session resumable. Pinned by `test_read_yes_no` (Task 8) and `test_eof_at_resume_prompt_leaves_session_resumable` (Task 8).
2. **A resumable session on a different subject** than the one being studied must not be offered. Pinned by `test_resumable_session_is_per_subject` (Task 5) and `test_other_subjects_session_is_not_offered` (Task 8).
3. **`EBB_TIMEZONE` written loosely**: surrounding spaces are stripped and an empty value means "unset"; a wrong name exits 2 with the exact message. Pinned by `test_resolve_timezone_*` (Task 2).
4. **Resuming the same session twice** (resume, the terminal is closed again, resume again) continues correctly and counts every answer exactly once. Pinned by `test_resume_twice_counts_each_answer_once` (Task 8).
5. **A day boundary in the learner's zone**: a review due at 20:00 UTC on Fri 16 Oct shows as `Sat 17 Oct` in `Asia/Dhaka`. Pinned by `test_format_day_uses_the_time_zone` (Task 2).

## File Map

| File | Change | Task |
|---|---|---|
| `backend/src/ebb/db/migrations/002_spaced_review.sql` | create | 1 |
| `backend/src/ebb/db/migrate.py` | `until` parameter | 1 |
| `backend/tests/conftest.py` | `pg_empty_pool` fixture | 1 |
| `backend/tests/test_migrations.py` | create | 1 |
| `backend/src/ebb/config.py` | `ebb_timezone`, `resolve_timezone` | 2 |
| `backend/src/ebb/ui.py` | time-zone-aware dates, greeting, resume text, yes/no parsing | 2, 8 |
| `backend/pyproject.toml`, `backend/uv.lock` | new dependencies | 2, 8 |
| `.env.example` | `EBB_TIMEZONE` | 2 |
| `backend/src/ebb/routing.py` | new pick order, no session cap, extend routing | 3 |
| `backend/src/ebb/domain.py` | `Answer`, `ResumableSession`; remove `Attempt` | 4, 5 |
| `backend/src/ebb/store.py`, `db/repo.py`, `tests/fakes.py` | new and changed `Store` methods | 4, 5 |
| `backend/src/ebb/llm/schemas.py`, `llm/tutor.py`, `llm/prompts/extend_concepts.md` | `extend_concepts` | 6 |
| `backend/src/ebb/graph/state.py`, `nodes.py`, `build.py` | `extend_subject`, avoid list, `record_answer`, duplicate fix | 7 |
| `backend/src/ebb/db/pool.py`, `cli.py` | `PostgresSaver`, housekeeping, greeting, resume, lock | 8 |
| `backend/tests/test_resume.py` | create | 8 |
| `README.md` | milestone 2 status, making a concept due | 9 |

---

### Task 1: Schema migration `002`

**Files:**
- Create: `backend/src/ebb/db/migrations/002_spaced_review.sql`
- Modify: `backend/src/ebb/db/migrate.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: milestone 1's `001_init.sql` and `migrate`.
- Produces: `migrate(conn, until: str | None = None) -> list[str]` (applies files whose name is `<= until` when given); fixture `pg_empty_pool` (fresh empty `public` schema, nothing migrated); columns `study_sessions.last_active_at timestamptz not null default now()` and `study_sessions.pruned_at timestamptz null`; unique constraints `(id, user_id)` on `subjects`, `concepts`, `study_sessions`, `questions`; composite foreign keys (all `on delete cascade`): `concepts (subject_id, user_id) → subjects (id, user_id)`, `study_sessions (subject_id, user_id) → subjects`, `questions (session_id, user_id) → study_sessions`, `questions (concept_id, user_id) → concepts`, `attempts (question_id, user_id) → questions`, `attempts (concept_id, user_id) → concepts`. They replace the single-column keys `concepts_subject_id_fkey`, `study_sessions_subject_id_fkey`, `questions_session_id_fkey`, `questions_concept_id_fkey`, `attempts_question_id_fkey`, `attempts_concept_id_fkey` (verified names in a real milestone 1 database). Back-fill `last_active_at` with the session's latest `attempts.graded_at` (via `questions.session_id`), else `started_at`.

- [ ] **Step 1: Add the fixture.** In `conftest.py`, add `pg_empty_pool(pg_session_pool)`: drop and recreate `public`, return the pool without migrating. Rewrite `pg_pool` to use it and then call `migrate`.

- [ ] **Step 2: Write the failing tests** in `test_migrations.py` (`pytestmark = pytest.mark.db`):

```python
def test_002_keeps_milestone_1_data(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        assert migrate(conn, until="001_init.sql") == ["001_init.sql"]
        seed_milestone_1(conn)  # raw SQL: 1 profile, subject, 2 concepts, session, question, attempt at T
        assert migrate(conn) == ["002_spaced_review.sql"]
        counts = row_counts(conn)  # per table
        session = conn.execute("select last_active_at from study_sessions").fetchone()
    assert counts == {"profiles": 1, "subjects": 1, "concepts": 2, "study_sessions": 1,
                      "questions": 1, "attempts": 1}
    assert session["last_active_at"] == T

def test_composite_keys_reject_a_child_owned_by_another_user(pg_pool):
    # Bob's concept pointing at Alice's subject
    with pytest.raises(psycopg.errors.ForeignKeyViolation): ...

def test_new_sessions_get_last_active_at(pg_pool): ...   # defaults to now(), pruned_at is null
def test_deleting_a_subject_still_cascades(pg_pool): ...  # concepts, sessions, questions, attempts gone
```

Run: test command on `tests/test_migrations.py`. Expected: FAIL (no `until` parameter / no `002`).

- [ ] **Step 3: Implement** `migrate(..., until=None)` and write `002_spaced_review.sql` with the columns, back-fill, unique constraints and key replacements above.

- [ ] **Step 4: Run** the full test command. Expected: all pass (milestone 1 tests included), 0 skipped; ruff clean.

- [ ] **Step 5: Commit** — `feat: add 002 migration with database-enforced ownership and session activity`.

---

### Task 2: Time zone

**Files:**
- Modify: `backend/src/ebb/config.py`, `backend/src/ebb/ui.py`, `backend/src/ebb/cli.py` (status only), `backend/pyproject.toml`, `.env.example`
- Test: `backend/tests/test_config.py` (create), `backend/tests/test_ui.py`

**Interfaces:**
- Produces: `Settings.ebb_timezone: str | None = None`; `resolve_timezone(name: str | None) -> ZoneInfo` in `ebb.config` (strip; empty → treat as unset; unset → `ZoneInfo(tzlocal.get_localzone_name())`, falling back to `ZoneInfo("UTC")` if that raises; a given name that `ZoneInfo` rejects (`ZoneInfoNotFoundError` or `ValueError`) → `ConfigError("EBB_TIMEZONE '{name}' is not a known time zone")`). In `ebb.ui`: `format_day(moment: datetime, tz: ZoneInfo) -> str` (`"%a %d %b"` after `astimezone(tz)`), and `tz: ZoneInfo` becomes a required parameter of `due_label`, `status_table` and `render_summary`, which all format dates through `format_day`. `ebb status` resolves the zone from settings (a `ConfigError` exits 2 via `_fail`).

- [ ] **Step 1: Add dependencies** `tzdata>=2026.5` and `tzlocal>=5.4.4,<6` to `pyproject.toml`; `uv lock`.

- [ ] **Step 2: Write the failing tests:**

```python
def test_resolve_timezone_accepts_a_name(): assert resolve_timezone("Asia/Dhaka") == ZoneInfo("Asia/Dhaka")
def test_resolve_timezone_strips_spaces(): assert resolve_timezone("  Asia/Dhaka ") == ZoneInfo("Asia/Dhaka")
def test_resolve_timezone_rejects_unknown_names():
    with pytest.raises(ConfigError, match="EBB_TIMEZONE 'Mars/Olympus' is not a known time zone"):
        resolve_timezone("Mars/Olympus")
def test_resolve_timezone_unset_uses_the_computer(monkeypatch):  # patch tzlocal.get_localzone_name → "Asia/Dhaka"; None and "" both give Dhaka
def test_resolve_timezone_falls_back_to_utc(monkeypatch):        # get_localzone_name raises → ZoneInfo("UTC")
def test_format_day_uses_the_time_zone():
    assert format_day(datetime(2026, 10, 16, 20, 0, tzinfo=UTC), ZoneInfo("Asia/Dhaka")) == "Sat 17 Oct"
def test_due_label_and_summary_use_the_time_zone(): ...          # same instant renders "Sat 17 Oct"
```

Update existing `test_ui.py` / `test_cli.py` calls for the new `tz` parameter (UTC keeps their expected strings).

- [ ] **Step 3: Implement** the above. In `.env.example` add, each comment on its own line: `# Time zone for dates, as an IANA name. In Docker the default is UTC.` then `# EBB_TIMEZONE=Asia/Dhaka`.

- [ ] **Step 4: Run** the full test command; ruff. Expected: all pass, 0 skipped.

- [ ] **Step 5: Commit** — `feat: show dates in the learner's time zone`.

---

### Task 3: Routing — pick order, no session cap, extend routing

**Files:**
- Modify: `backend/src/ebb/routing.py`
- Test: `backend/tests/test_routing.py`

**Interfaces:**
- Produces (pure, over plain state mappings):
  - `pick_next_concept(concepts, studied_ids, now) -> Concept | None` (signature unchanged). Order: due reviews (stage ≥ 1 and `is_due`) by lowest `tide`, ties by list order (the store returns `position` order); then stage-0 concepts in list order; learned concepts that are not due are never returned; `studied_ids` excluded.
  - `CONCEPTS_PER_SESSION` removed; a finished concept always routes to `"next_concept"`. The state keys `concepts_done` and `concept_count` are no longer read.
  - `after_pick(state) -> Literal["generate_questions", "extend_subject", "summarize"]`: concept present → generate; else `"summarize"` if `state["caught_up"]` else `"extend_subject"`.
  - `after_extend(state) -> Literal["pick_concept", "summarize"]`: `"summarize"` if `state["caught_up"]` or `state["fatal"]`, else `"pick_concept"`.
  - `after_mastery(state)`: when `state["grade"]` is `None` (duplicate answer), return `decide_next(state)`.

- [ ] **Step 1: Write the failing tests** (replace the milestone 1 pick-order and session-cap tests):

```python
def test_due_reviews_come_before_new_concepts(): ...      # new stage-0 listed first, due stage-2 still picked
def test_most_faded_review_first():                       # stage 2 reviewed 10 days ago (tide≈0.30) beats 4 days ago (≈0.62)
def test_equal_tide_keeps_list_order(): ...
def test_new_concepts_in_list_order_when_nothing_is_due(): ...
def test_learned_concepts_that_are_not_due_are_never_picked(): assert pick_next_concept([not_due_stage_2], [], NOW) is None
def test_studied_concepts_are_skipped(): ...
def test_finished_concept_always_moves_on(): assert decide_next(state(correct_streak=2, studied_concept_ids=list("abcdefgh"))) == "next_concept"
def test_after_pick_extends_when_nothing_is_left(): assert after_pick({"concept": None, "caught_up": False}) == "extend_subject"
def test_after_pick_summarizes_when_caught_up(): assert after_pick({"concept": None, "caught_up": True}) == "summarize"
def test_after_extend(): ...                              # pick_concept / summarize on caught_up / summarize on fatal
def test_duplicate_answer_routes_like_an_uncounted_answer(): assert after_mastery(state(grade=None)) == "ask_question"
```

- [ ] **Step 2: Run** `tests/test_routing.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep the other routing functions unchanged.
- [ ] **Step 4: Run** `tests/test_routing.py` and `tests/test_mastery.py`. Expected: PASS. (`test_graph.py` is updated in Task 7; it may fail until then. Do not edit it here.)
- [ ] **Step 5: Commit** — `feat: review faded concepts first and drop the session cap`.

---

### Task 4: Store — answers under a row lock, subjects and prompts

**Files:**
- Modify: `backend/src/ebb/domain.py`, `backend/src/ebb/store.py`, `backend/src/ebb/db/repo.py`, `backend/tests/fakes.py`
- Test: `backend/tests/test_db.py`, `backend/tests/test_fakes.py` (create; only for `MemoryStore` parity)

**Interfaces:**
- Produces in `ebb.domain`: `Answer(id: UUID, question_id: UUID, concept_id: UUID, answer_text: str, grade: GradeValue, feedback: str, model_used: str, graded_at: datetime)` (frozen dataclass). `Attempt` is removed.
- Produces on `Store` (and `PgStore`, `MemoryStore`):
  - `record_answer(user_id, answer: Answer) -> Concept | None` replaces `record_attempt`. One transaction: ownership checks as today (`LookupError`); `select … for update` on the concept row; `after = mastery.apply_grade(row, answer.grade, answer.graded_at)`; insert the attempt with `stage_before=row.stage`, `stage_after=after.stage` (`on conflict (id) do nothing`); on a duplicate return `None` with nothing changed; otherwise update the concept, set the question's session `last_active_at = answer.graded_at`, and return `after`.
  - `answered_prompts(user_id, concept_id, limit: int = 40) -> list[str]`: distinct prompts of questions that have an attempt, newest attempt first.
  - `get_subject(user_id, subject_id) -> Subject | None`.
  - `add_concepts(user_id, subject_id, concepts: Sequence[tuple[str, str]]) -> int`: drops names equal (case-insensitive, stripped) to an existing concept's or to an earlier one in the batch; new rows at stage 0 with positions after the current maximum; returns how many were added; `LookupError` if the subject is not the user's.
  - `count_due(user_id, subject_id, now) -> int` (stage ≥ 1 and due), `count_new(user_id, subject_id) -> int` (stage 0), `next_review_at(user_id, subject_id) -> datetime | None` (earliest among stage ≥ 1), `set_timezone(user_id, name: str) -> None` (`profiles.timezone`).

- [ ] **Step 1: Write the failing database tests** (replace the milestone 1 `record_attempt` tests):

```python
def test_record_answer_grades_the_current_row_not_a_snapshot(store):
    # concept due at stage 3; answer A incorrect → stage 1 (+1 day)
    # answer B correct, from a session that still "saw" stage 3 → row stage 1 is not due → unchanged
    assert store.record_answer(ALICE, answer_b).stage == 1
    assert attempt_row(answer_b)["stage_before"] == 1 and attempt_row(answer_b)["stage_after"] == 1
def test_duplicate_answer_returns_none_and_changes_nothing(store): ...
def test_record_answer_touches_the_session(store): ...          # last_active_at == graded_at
def test_record_answer_refuses_other_users(store): ...          # LookupError, no BOB rows
def test_answered_prompts_only_answered_newest_first(store): ... # unanswered batch question excluded; limit respected
def test_add_concepts_drops_duplicates_and_appends(store):      # existing "DNS" vs "dns ", in-batch dupes; positions continue
def test_counts_and_next_review(store): ...                      # count_due, count_new, next_review_at
def test_get_subject_is_scoped(store): assert store.get_subject(BOB, alice_subject) is None
```

`test_fakes.py`: the same scenarios for `record_answer`, `add_concepts` and `answered_prompts` against `MemoryStore`, so the fake keeps the contract.

- [ ] **Step 2: Run** `tests/test_db.py tests/test_fakes.py`. Expected: FAIL.
- [ ] **Step 3: Implement** in `domain.py`, `store.py`, `repo.py` and `fakes.py`. Remove `record_attempt`. Graph callers are updated in Task 7.
- [ ] **Step 4: Run** `tests/test_db.py tests/test_fakes.py tests/test_migrations.py`. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: grade answers against the locked database row`.

---

### Task 5: Store — sessions, pruning and the terminal lock

**Files:**
- Modify: `backend/src/ebb/domain.py`, `backend/src/ebb/store.py`, `backend/src/ebb/db/repo.py`, `backend/tests/fakes.py`
- Test: `backend/tests/test_db.py`, `backend/tests/test_fakes.py`

**Interfaces:**
- Produces in `ebb.domain`: `ResumableSession(session_id: UUID, thread_id: str, last_active_at: datetime)`.
- Produces in `ebb.store`: constants `RESUME_WINDOW = timedelta(hours=24)` and `PRUNE_AFTER = timedelta(days=7)`, and on `Store` (and both implementations):
  - `resumable_session(user_id, subject_id, now) -> ResumableSession | None`: `ended_at is null` and `last_active_at > now - RESUME_WINDOW`; the most recent by `last_active_at`.
  - `close_stale_sessions(user_id, now) -> int`: sets `ended_at = last_active_at` where `ended_at is null` and `last_active_at <= now - RESUME_WINDOW`.
  - `threads_to_prune(user_id, now) -> list[str]` (`ended_at <= now - PRUNE_AFTER` and `pruned_at is null`), and `mark_pruned(user_id, thread_ids: Sequence[str], now) -> None`.
  - `session_lock(user_id, thread_id) -> AbstractContextManager[bool]`: yields `True` if this process now holds the lock, `False` if another holds it, and releases on exit. `PgStore` uses `pg_try_advisory_lock` / `pg_advisory_unlock` with a stable 64-bit key derived from `thread_id`, on a connection taken from the pool for the lock's lifetime. `MemoryStore` keeps a set.

- [ ] **Step 1: Write the failing tests:**

```python
def test_resumable_within_24_hours(store): ...                 # last_active 23h59m ago → offered; exactly 24h → None
def test_resumable_session_is_per_subject(store): ...          # open session on subject A is not offered for B
def test_most_recent_resumable_session_wins(store): ...
def test_ended_sessions_are_not_resumable(store): ...
def test_close_stale_sessions_uses_last_activity(store): ...   # ended_at == last_active_at; returns count
def test_threads_to_prune_after_7_days_once(store): ...        # listed, mark_pruned, then not listed
def test_session_lock_refuses_a_second_holder(store):
    with store.session_lock(ALICE, "t-1") as first:
        with store.session_lock(ALICE, "t-1") as second:
            assert first is True and second is False
    with store.session_lock(ALICE, "t-1") as again:
        assert again is True
```

Mirror the window, per-subject and lock tests in `test_fakes.py`.

- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** `create_session` keeps its signature; `last_active_at` comes from the column default.
- [ ] **Step 4: Run** `tests/test_db.py tests/test_fakes.py`. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: track resumable sessions and lock a session to one terminal`.

---

### Task 6: Tutor — extending a subject

**Files:**
- Create: `backend/src/ebb/llm/prompts/extend_concepts.md`
- Modify: `backend/src/ebb/llm/schemas.py`, `backend/src/ebb/llm/tutor.py`, `backend/tests/fakes.py`
- Test: `backend/tests/test_schemas.py`, `backend/tests/test_tutor.py`

**Interfaces:**
- Produces: `ConceptExtension(BaseModel)` with `concepts: list[ConceptDraft]`, validated to 3–5 items by a validator (not a `Field` constraint, as milestone 1's schemas do). On `Tutor` and `LangChainTutor`: `extend_concepts(subject: str, existing: Sequence[Concept]) -> Generated[list[ConceptDraft]]`, creative temperature, through `_run` with `parse=lambda out: out.concepts`. The prompt template takes `$subject` and `$existing`, one `- name: description` line per existing concept, and asks for 3–5 further concepts that build on them, are more advanced, are ordered foundational to advanced and duplicate none of them. `FakeTutor` gains `extensions: list[list[str]] | None = None` (names returned per call, in order; once the list is used up, or when it is `None`, a call returns no concepts) and `fail_extend: bool = False`, records `extend_calls`, and raises `AllProvidersExhausted` when `fail_extend`. With the defaults, a session that learns every concept therefore ends caught up, which keeps milestone 1's graph tests finite.
- The spec says `existing: list[ConceptDraft]`; the plan passes domain `Concept`s, which the graph already holds. This is the same information.

- [ ] **Step 1: Write the failing tests:**

```python
def test_concept_extension_needs_three_to_five(): ...                # 2 and 6 rejected, 3 and 5 accepted
def test_extend_concepts_uses_structured_output_and_the_prompt():
    out = tutor.extend_concepts("Networks", [concept("DNS", "Names to addresses.")])
    assert [c.name for c in out.value] == ["BGP", "QUIC", "SDN"]
    assert creative.calls[0][0] is ConceptExtension
    assert "Networks" in creative.calls[0][1] and "- DNS: Names to addresses." in creative.calls[0][1]
def test_every_prompt_fills_all_placeholders(...):                   # add ("extend_concepts", {"subject": ..., "existing": ...})
```

- [ ] **Step 2: Run** `tests/test_schemas.py tests/test_tutor.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: ask the tutor for more advanced concepts`.

---

### Task 7: Graph — extend, fresh questions, answers through the store

**Files:**
- Modify: `backend/src/ebb/graph/state.py`, `backend/src/ebb/graph/nodes.py`, `backend/src/ebb/graph/build.py`
- Test: `backend/tests/test_graph.py`

**Interfaces:**
- Consumes: Task 3 routing (`after_pick`, `after_extend`), Task 4 `record_answer` / `answered_prompts` / `add_concepts` / `get_subject`, Task 6 `extend_concepts`.
- Produces:
  - State gains `caught_up: bool` (initialised `False` in `load_learner`) and drops `concept_count` and `concepts_done`.
  - New node `extend_subject`: loads the subject and its concepts; calls `tutor.extend_concepts`; `AllProvidersExhausted` → `fatal = EXHAUSTED_NOTICE` plus the notice; otherwise `store.add_concepts(...)`, setting `caught_up = True` when it returns 0. `ConfigError` propagates.
  - `generate_questions` passes `answered_prompts(...)` (limit 40) followed by this session's `asked_prompts`, de-duplicated, as the avoid list.
  - `update_mastery` builds an `Answer` and calls `record_answer`. `None` → `{"grade": None, "lesson": None}`; otherwise it stores the returned concept and updates tally, streak and `asked_on_concept` as today.
  - `build_graph` wiring: `pick_concept → after_pick → generate_questions | extend_subject | summarize`; `extend_subject → after_extend → pick_concept | summarize`; `next_concept → pick_concept`. Ten nodes; update the module docstring.

- [ ] **Step 1: Update and add graph tests** (offline, `FakeTutor` + `MemoryStore`). Replace the 3-concept-cap test:

```python
def test_session_runs_past_three_concepts_until_quit(): ...       # 5 concepts, all right, quit at payload 9 → 4 results
def test_due_review_comes_first(): ...                            # seed "Concept 3" stage 2 due → first payload concept is "Concept 3"
def test_learning_everything_extends_the_subject():
    tutor = FakeTutor(extensions=[["Advanced 1", "Advanced 2", "Advanced 3"]])
    # 1 concept answered right twice → extend → next payload concept == "Advanced 1"
def test_extension_with_nothing_new_ends_caught_up(): ...         # extensions=[["Concept 1"]] → final["caught_up"] is True, no further payloads
def test_extension_failure_ends_with_the_exhausted_notice(): ...  # fail_extend → final["fatal"] == EXHAUSTED_NOTICE
def test_avoid_list_includes_earlier_sessions(): ...              # prompts answered in session 1 appear in FakeTutor's avoid list in session 2
def test_duplicate_answer_leaves_no_stale_feedback():
    # store whose record_answer returns None → next payload has feedback None and lesson None
```

`FakeTutor.generate_questions` records the `avoid` list it receives (`avoid_calls`).

- [ ] **Step 2: Run** `tests/test_graph.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full test command; ruff. Expected: all pass, 0 skipped. (Sessions in existing tests now run until every concept is learned and then end caught up, because the default `FakeTutor` extends with nothing; adjust their expected counts where they assumed the 3-concept cap.)
- [ ] **Step 5: Commit** — `feat: extend subjects and never repeat answered questions`.

---

### Task 8: CLI — checkpointer, greeting, resume, lock

**Files:**
- Modify: `backend/src/ebb/cli.py`, `backend/src/ebb/ui.py`, `backend/src/ebb/db/pool.py`, `backend/pyproject.toml`
- Create: `backend/tests/test_resume.py`
- Test: `backend/tests/test_cli.py`, `backend/tests/test_ui.py`

**Interfaces:**
- Consumes: everything above; `langgraph.checkpoint.postgres.PostgresSaver` (verified: works on the existing pool, `setup()` is idempotent, a new saver instance on the same pool reads `graph.get_state(config).interrupts` and resumes, `delete_thread(thread_id)` removes a thread).
- Produces:
  - Dependency `langgraph-checkpoint-postgres>=3.2.0,<4`; `make_pool` adds `"prepare_threshold": 0` to the connection kwargs, as `PostgresSaver` requires.
  - `cli._checkpointer(store: PgStore) -> BaseCheckpointSaver`: `PostgresSaver(store.pool)`, `setup()` once per process. This is the seam tests replace with a shared `InMemorySaver`.
  - `ebb.ui`: `greeting(due: int, new: int) -> str`, `format_ago(delta: timedelta) -> str`, `resume_question(payload: dict, ago: str) -> str` and `read_yes_no(raw: str) -> bool | None`, with the exact copy and rules from Global Constraints.
  - `ebb study` order: resolve the time zone (`set_timezone` with its name); `close_stale_sessions`; prune (`threads_to_prune` → `delete_thread` each → `mark_pruned`); choose the subject; `resumable_session`. If one is found: take `session_lock`. Locked elsewhere → print the "open in another terminal" line and start fresh. No pending interrupt in `get_state` → `end_session` and start fresh. Otherwise ask `resume_question` with the pending payload: yes → render that payload and continue the loop on the same thread; no → `end_session` and start fresh; EOF or Ctrl+C at the prompt → exit 0, nothing changed. A fresh session gets a new thread, takes the lock, prints `greeting(count_due, count_new)`, and invokes as in milestone 1. The lock is held until the command exits. After the summary, if `final["caught_up"]`, print the caught-up line using `next_review_at` and `format_day`.

- [ ] **Step 1: Write the failing tests.**

`test_ui.py`:

```python
@pytest.mark.parametrize(("raw", "expected"), [("", True), ("y", True), ("YES", True), (" n ", False), ("No", False), ("maybe", None)])
def test_read_yes_no(raw, expected): assert read_yes_no(raw) is expected
@pytest.mark.parametrize(("delta", "text"), [(timedelta(seconds=20), "a moment ago"), (timedelta(minutes=1), "1 minute ago"),
    (timedelta(minutes=12), "12 minutes ago"), (timedelta(hours=1), "1 hour ago"), (timedelta(hours=3, minutes=50), "3 hours ago")])
def test_format_ago(delta, text): assert format_ago(delta) == text
def test_greeting_variants(): ...   # (3, 2), (1, 0), (0, 4), (0, 0) → the four exact strings
def test_resume_question(): assert resume_question({"concept": "DNS resolution", "number": 2}, "3 hours ago") == \
    "You stopped on DNS resolution, question 2, 3 hours ago. Resume? [Y/n]"
```

`test_cli.py` (fakes; the fixture's settings are `Settings(_env_file=None, ebb_timezone="UTC")` so dates do not depend on the machine; `_checkpointer` returns one shared `InMemorySaver`; simulate a closed terminal by making `cli._ask` raise `RuntimeError` on a chosen question, so the command exits without reaching `summarize`):

```python
def test_resume_yes_continues_at_the_same_question(fakes): ...     # second run shows the resume line, then the same prompt text
def test_resume_no_starts_fresh(fakes): ...                         # old session ended; greeting printed
def test_eof_at_resume_prompt_leaves_session_resumable(fakes): ...  # exit 0; resumable_session still returns it
def test_locked_session_starts_fresh(fakes): ...                    # lock pre-held → "That session is open in another terminal."
def test_other_subjects_session_is_not_offered(fakes): ...
def test_q_ends_the_session_and_is_not_offered_again(fakes): ...
def test_greeting_shows_due_count(fakes): ...                       # seeded due concept → "Welcome back — 1 idea is ebbing…"
def test_caught_up_line_uses_the_next_review_date(fakes): ...
```

`test_resume.py` (`pytest.mark.db`; real `PgStore`, real `PostgresSaver`, `FakeTutor`):

```python
def test_resume_from_a_new_process():
    # run to the 2nd question; drop the graph and saver; new PostgresSaver on the same pool;
    # get_state(config).interrupts[0].value["number"] == 2; resume, quit → attempts == answers given
def test_resume_twice_counts_each_answer_once(): ...
def test_state_without_a_pending_question_is_not_resumable(): ...   # CLI path closes it (driven through cli with real saver)
```

- [ ] **Step 2: Run** `tests/test_ui.py tests/test_cli.py tests/test_resume.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full test command and ruff, then the container check: `docker compose build ebb` and `docker compose run --rm -T ebb status`. Expected: all pass, 0 skipped; `status` prints the owner's existing subject, with `002` applied on the way.
- [ ] **Step 5: Commit** — `feat: resume interrupted sessions and greet with what is due`.

---

### Task 9: Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** Update "Status" (milestone 2 of 6 built: spaced review and resumable sessions; next: notes upload with cited answers). Remove the hard-coded test count from the development block. Add a short "Try spaced review now" development note:

```bash
# Pretend a week has passed: every learned concept becomes due
docker compose exec db psql -U ebb -d ebb -c \
  "update concepts set last_reviewed_at = last_reviewed_at - interval '7 days', next_review_at = next_review_at - interval '7 days' where stage > 0"
```

Mention `EBB_TIMEZONE` in "Run it".

- [ ] **Step 2:** Run the full test command once more (docs only; this confirms a clean tree). Expected: all pass.
- [ ] **Step 3: Commit** — `docs: document spaced review, resume and EBB_TIMEZONE`.

---

### Task 10: Real-model check, push and CI (with the owner)

- [ ] **Step 1:** With the owner's `.env`: `docker compose build ebb`, then `docker compose run --rm ebb study`. Answer one question; at the next one, close the terminal window. Run `docker compose run --rm ebb study` again. Expected: the resume line names that question; answering `y` continues there.
- [ ] **Step 2:** Type `q` to end; run `study` again. Expected: no resume offer; greeting shown.
- [ ] **Step 3:** Run the README's "pretend a week has passed" command, then `study`. Expected: `Welcome back — {n} ideas are ebbing…`, and the first question is about a previously learned concept.
- [ ] **Step 4:** `docker compose run --rm ebb status`. Expected: the owner's milestone 1 concepts are still there, with dates in their `EBB_TIMEZONE`.
- [ ] **Step 5:** With the owner's OK, push the branch, `gh run watch --exit-status`, then finish the branch (superpowers:finishing-a-development-branch).
