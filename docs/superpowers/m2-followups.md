# Milestone 2 — follow-ups

Items found while building and reviewing milestone 2 that were deliberately left for
later. Earlier items: [m1-followups.md](m1-followups.md).

## Behaviour worth revisiting

- **Subjects can grow by near-duplicates.** `extend_concepts` must return 3–5 concepts and
  `add_concepts` only drops exact (case-insensitive) name matches, so rewordings slip in and
  "all caught up" is rarely reached. Options: let the tutor return "nothing more", or check
  similarity before adding.
- **Resume prompt numbering after a cut-off lesson.** If Ebb stopped during `teach`, the prompt
  names question N+1; a stop inside `pick_concept` names the concept just finished.
- **Housekeeping ignores a live lock.** A terminal left at a question for more than 24 hours has
  its session closed by another terminal's startup (`last_active_at` only moves on graded
  answers). Touch it when a question is shown, or skip sessions whose lock is held.
- **Time zone names on Windows.** `ZoneInfo("utc")` is accepted with that casing and stored as-is;
  canonicalise, and don't overwrite the stored zone with the UTC fallback.

## Before the milestone 4 API (long-lived process)

Resolved in milestone 4 unless marked open.

- ~~The pool `reset` callback (`pg_advisory_unlock_all`) routes every connection return through a
  pool worker; for a server, close the lock connection on error inside `session_lock` instead.~~
  Resolved (Task 9).
- ~~The due rule exists in `mastery.is_due` and again in SQL (`count_due`); keep them in step or
  derive the counts from `list_concepts`.~~ Resolved: counts derive from `reviews_due` (Task 8).
- Ctrl+C during startup housekeeping or `get_state` prints a traceback. **Still open** (listed in
  [m4-followups.md](m4-followups.md)).
- ~~The resume branching in `cli.study` could become one "first graph input" decision.~~
  Resolved: the CLI and the API share `StudySessions` (Task 13).

## Before milestone 6 (remote database)

- `last_active_at` mixes the database clock (`default now()`) and the client clock (`graded_at`);
  unify before a remote database.
- Composite foreign-key columns have no supporting indexes (fine at this scale).
- `select … for update` in `record_answer` could be `for no key update`.

## Test gaps (low priority)

- Cross-user foreign-key tests violate two keys per insert; assert `diag.constraint_name`.
- No exact 24-hour boundary test for `close_stale_sessions`; a refused lock holder exiting
  leaving the first holder intact is untested.
- No tests for `ConfigError` propagating out of `extend_subject`, or avoid-list de-duplication
  when a prompt is both stored and asked this session.
- `google-genai` emits a Python 3.14 `DeprecationWarning` in test output.
