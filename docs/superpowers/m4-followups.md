# Milestone 4 — follow-ups

Minor items found while building and reviewing milestone 4 that were deliberately left for later,
grouped. Earlier items: [m1-followups.md](m1-followups.md), [m2-followups.md](m2-followups.md),
[m3-followups.md](m3-followups.md). The decisions taken on the owner's behalf during milestone 4
are in the SDD ledger (rulings R1–R42).

## Worth a look with the owner

- Onboarding's "add notes" page has some invented copy (eyebrow, title, lede) and a subject list
  in its aside that the prototype doesn't show.
- The subject gauge in the left column fills to (concepts - due) / concepts, a new metric drawn
  as a tide gauge; "due yesterday" wording on the same card.
- The grade card omits the prototype's tide note ("DNS resolution stays at 52%"), because the
  turn payload has no tide before and after (R30).
- On the dashboard, a fall in Average tide is coloured amber (`--partial`); apostrophes are
  straight, not the prototype's curly ones.
- A citation popover says "p. 12" (the API's label) where the prototype says "page 12".

## Backend: graph and sessions

- `routing.py` still reads defaulted keys with `state.get` (routers run on raw state), which
  duplicates `STATE_DEFAULTS`; `fill_defaults` is shallow for keys that are present.
- A grade, lesson or re-check fix carries only `retried.fell_back`, so the backup-model notice is
  lost if only the first call fell back (one line: OR `fell_back` inside `fix_citations`).
- `Generated(candidates, "")` placeholder; `_failing` is computed twice per fix.
- `concepts_from_notes` and `extend_concepts` lost their "Subject:" and "Concepts already covered"
  labels in the merged prompts.
- `"new"` (stage 0) is counted inline in `cli.py`; a `mastery.new_concepts` helper would keep it
  in one place.
- `study.py` is past R14's length guideline (323 lines).
- Offer, discard and hold lock probes can make a concurrent real reply hit `SessionBusy` briefly.
- A malformed dispute id reaching `_applied` raises `ValueError` (the route validates it first).
- `_is_refusal`'s `additional_kwargs["refusal"]` check is a no-op for `langchain_groq`, which
  never sets it.
- A refused grade re-queues the same question for the rest of the session (the spec's
  behaviour); the only way out is End session.
- `citations.from_dict` raises `KeyError` on a partial dict that has `name` but no `heading` or
  `title`.
- `CappedTutor.take_ai_call` has no threaded atomicity test.
- `ebb study` Ctrl+C during startup housekeeping or `get_state` still prints a traceback (from
  [m2-followups.md](m2-followups.md)).
- A model that refuses in plain text is still treated as malformed output (retried, then
  exhausted), because there is no reliable signal (R11).

## Backend: API and store

- `LookupError` catch-all turns an accidental `KeyError`/`IndexError` in a route into a 404.
- `POST /subjects` answering 503 (no tutor) is untested; no test for newest-first subject order.
- `list_subjects` runs `list_concepts` per subject (N+1; fine at this scale).
- Concurrent uploads to one draft are untested; open upload streams hold threadpool workers
  (pool of 40); a bad file sent to an expired draft gets the file refusal before the 410.
- Oversized uploads without a `Content-Length` header are still spooled before refusal (R21).
- `build_progress` filters answers per concept (O(concepts x answers)); no store-level test that
  voided answers are excluded from the streak and `enough_days`; API tests run in UTC only.
- The tide replay trusts the caller to pass one concept's answers; `average_tide` has chained
  ifs; `tide_line` grid edges and `start >= now` are untested.
- `MemoryStore` tests poke at internals; the `_insert_subject` other-`UniqueViolation` branch and
  a rename tie on equal `created_at` are untested.
- The pool test reads a private (`pool._reset`), and the unlock-failure test may race
  `pg_terminate_backend`.
- The first full-suite run once stalled at
  `test_cli::test_a_resumed_session_holds_its_lock_while_it_runs` (probably two runs sharing
  `ebb_test`); watch for a real hang.
- A pre-existing nit: `nodes.py` imports `ebb.citations` `from_dict as citation_from_dict`
  separately from the rest (ruff keeps `as` imports apart).

## Frontend

- The header starts "light" and flips, so a dark reload can show a one-frame icon flicker.
- Dispute dialog doesn't lock page scroll on desktop.
- The stage tooltip (240 px from its trigger) spills past the Memory column edge; Escape doesn't
  close the mobile drawer while focus is on the menu button (the button toggles it).
- The composer's `isComposing` check misses Safari's `keyCode` 229.
- A `TypeError` is reported as "Can't reach the API."; reducers run in `setState` updaters
  without an error boundary.
- Onboarding: Start over during an in-flight `createDraft` is unhandled.
- Dark-mode dashboard screenshots are from round 1 of Task 25.
- `npm audit` reports a high advisory in `eslint-config-next`'s transitive `braces` (dev only).

## Test gaps

- Through a stream: step labels for `search_web`, `teach`, `regrade` and `extend_subject` are
  tested only via the `step_label` table; a fallback on a rewrite only; web-mode generation
  refusal; a stale legacy `retry_question` / `pending_question_id` not re-applied; a dispute
  during a failed-grade retry and a failed grade after a dispute; legacy `run` candidates being a
  copy.
- Not covered: `CappedTutor`'s `.astimezone(tz)` in a non-UTC zone; exit 2 for a bad
  `EBB_TIMEZONE` in `new` / `add` (R13); a Dhaka-zone end-to-end check.
- Frontend: Retry on the add page; the "5 of 5" announcement; the progress page (skeleton, 404,
  Retry, abort); silent reconnect on 404; unmount abort; keys ignored on buttons and popover.
- `FakeTutor.regrades` is still `(str, bool)` tuples; `summary_tally` is defined after first use
  in `test_graph.py`; the e2e browser test runs one session against the fake tutor only (no
  uploads, disputes or dark mode).
- The study-route disconnect test hangs, rather than failing, if a change stops the events being
  closed.
- The e2e test's `waiting` locator could hit Playwright's strict mode if the UI changes; CI
  doesn't cache the Playwright browsers.
