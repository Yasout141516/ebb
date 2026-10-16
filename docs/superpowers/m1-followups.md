# Milestone 1 — follow-ups

Items found while building and reviewing milestone 1 that were deliberately left
for later. Milestone 2 planning should start from the first section.

## For milestone 2 (spaced review, session resume)

- **Overdue reviews wait behind new concepts.** `routing.pick_next_concept` treats
  stage-0 concepts as due, and lower stage wins, so overdue stage-1+ reviews come
  after every new concept has been introduced. Most spaced-repetition tools show
  overdue reviews first. This is a spec change, not a bug.
- **Database-enforced ownership (E9).** Add `unique (id, user_id)` on parent tables
  and composite foreign keys such as `(subject_id, user_id) references
  subjects (id, user_id)` on children, as a `002_` migration. Today the repository's
  in-transaction ownership checks enforce it in code.
- **Lost update across concurrent sessions.** `update_mastery` writes absolute
  values computed from the graph's snapshot of the concept, so two simultaneous
  `ebb study` runs on one subject can overwrite each other. Compute from the row
  (`select … for update`) or take E3's per-thread advisory lock.
- **Stale grade after a duplicate resume.** When `record_attempt` reports a
  duplicate, `update_mastery` returns `{}` and the previous grade stays in state.
  Unreachable with one CLI process; handle it together with `PostgresSaver` resume.
- **Questions can repeat across sessions.** `asked_prompts` resets per concept per
  session; cross-session avoidance belongs with spaced review.
- **Sessions left open.** Ctrl+C or a closed terminal leaves
  `study_sessions.ended_at` null; resume and checkpoint pruning (E8) own this.
- **"Due today" in the learner's time zone (E9).** Dates are UTC in milestone 1.

## Before the milestone 4 API

- Validate resume payloads at the API boundary (a malformed dict raises `KeyError`).
- Make the grading prompt's `ANSWER>>>` fence unclosable and fence `$answer` in
  `teach.md`.
- Per-user daily LLM cap (`UserQuotaExceeded`, E6) and streaming failure handling
  (`StreamInterrupted`).
- `_fail` prints to stdout; errors should go to stderr once output is scripted.

## Small cleanups, any time

- `make_pool` catches only `OperationalError`: a malformed `DATABASE_URL` gives a
  traceback and a wrong password says "Is it running?". Catch `psycopg.Error` and
  show the first line of the reason.
- A malformed `EBB_DEV_USER_ID` raises a pydantic `ValidationError` traceback.
- `ebb new` with an existing subject name creates a duplicate (newest wins).
- Schema validators do not reject an empty concept description or question prompt;
  add them if the real models ever produce one.
- Rich `Text` objects instead of `escape()` at each call site would make markup
  escaping impossible to forget.
- Test gaps: `is_due` at exactly `next_review_at == now`; direct unit tests for the
  small edge functions in `routing.py`; the Ctrl+C-during-an-LLM-call exit code.
- `test_backup_model_notice_is_shown_once` is misnamed; the README's test count will
  go stale.
- `google-genai` emits a Python 3.14 `DeprecationWarning` (`_UnionGenericAlias`);
  it goes away when Google updates the SDK.
