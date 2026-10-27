# Milestone 4 — rulings made on the owner's behalf

Decisions taken during the milestone 4 build (subagent-driven development, 2026-10-23 to
2026-10-27) where the spec or plan left a choice open or conflicted. Each line: the decision,
then what it costs if it turns out wrong. Owner's own decisions are in the
[design](specs/2026-10-23-web-app-design.md), [plan-reviews.md](../plan-reviews.md)
("Amendments in milestone 4") and [HANDOFF.md](../HANDOFF.md).

## Sessions, replies and leases

1. **R1** Sending the same answer or dispute twice (double-click, Retry) returns the current question instead of moving on. *If wrong:* a deliberate identical resend is ignored.
2. **R2** A multiple-choice answer is checked against the pending question's options before the graph runs (422 otherwise). *If wrong:* one extra read.
3. **R25** A resent answer skips that pre-check, so a retry never gets a false 422. *If wrong:* none.
4. **R29** Retry after "The tutor couldn't respond" re-sends the answer under a new attempt id (the first was never recorded); transport retries reuse the same id. *If wrong:* none.
5. **R26 / R40** Start fresh never ends a session another tab or terminal holds; it ends the caller's own old session when nothing is running in it. *If wrong:* none.
6. **R41** A tab releases its lease on `pagehide`; a refreshed tab whose turn is still finishing retries for about 30 s ("Finishing your last answer…") before showing the offer. *If wrong:* a slower reconnect.
7. **R43** The release call carries the tab's driver id as a query parameter (a JSON body would need a CORS preflight, unreliable while a page closes). *If wrong:* a small switch to a body.
8. **R45** (supersedes R44) After a reader leaves mid-turn, the server finishes the turn but never claims or renews a lease. *If wrong:* none.
9. **R14** `study.py` stays one module past the ~300-line guideline. *If wrong:* a later split.
10. **R46** Deferred: a duplicated browser tab shares its driver id (sessionStorage is copied); `GET /subjects` N+1 and per-turn sources refetch; `build_progress` O(concepts × answers). *If wrong:* follow-ups (see m4-followups.md).

## Graph clean-ups (Phase A)

11. **R7** "Reading your notes…" is only shown when the subject has notes. *If wrong:* one missing step line.
12. **R8** A milestone-3 session saved mid-citation-retry regenerates its batch on resume (one more generation call on that concept). *If wrong:* fewer questions in that one resumed session.
13. **R9** The summary's "tide before" is measured at the concept's first answer this session. *If wrong:* a fraction of a point.
14. **R10** No insertion-order column on attempts; identical `graded_at` ties fall back to attempt id. *If wrong:* only frozen test clocks tie.

## Safety, cap and the CLI

15. **R3** In `ebb study`, a refusal or the daily cap ends the session normally (notice, summary, exit 0); `ebb new`/`add` exit 1. *If wrong:* exit codes differ from the plan text.
16. **R5** The cap or a refusal mid-session reaches the browser as a notice inside the stream, not as an `error` event. *If wrong:* test naming only.
17. **R11** Refusals are detected by Groq's documented `finish_reason: "content_filter"` (plus `additional_kwargs["refusal"]`); a plain-text refusal stays a parse failure. *If wrong:* such a refusal shows the exhausted notice.
18. **R12** The CLI's notes commands print "The tutor won't help with these notes." on a refusal. *If wrong:* wording.
19. **R13** A bad `EBB_TIMEZONE` also stops `ebb new`/`add` (the cap counts days in the profile zone). *If wrong:* the zone must be fixed first.
20. **R4** A UI helper (`ui.pages_prefix`) was made public instead of imported privately. *If wrong:* none.

## API

21. **R6 / R16** The website calls `/health` on load; `/health` checks the database for real; a key, database or time-zone problem shows full width. *If wrong:* message placement; one query.
22. **R15** `subjects.add_notes` refuses a full subject or a duplicate file before any AI call (the store re-checks). *If wrong:* one duplicated check.
23. **R18** A draft file's "ready" event lists every concept found in it (`concepts`) plus the new ones (`new`). *If wrong:* the row shows more names than the subject gains.
24. **R19** The full/duplicate/draft-limit refusal copy lives in one shared module. *If wrong:* none.
25. **R20** Accepted: draft methods take `user_id`; refusals carry `retry`; committing an empty draft says "Add a file first."; an upload finishes even if the reader leaves; no SSE keep-alive pings. *If wrong:* small API differences.
26. **R21** Oversized uploads are refused from `Content-Length` before the body is parsed. *If wrong:* a client that sends no length still spools first.
27. **R22** Files saved together keep their upload order (`clock_timestamp()`). *If wrong:* none.
28. **R23 / R33** A config error arrives as `error {error}` (full-width message); transient failures (database down, unexpected) arrive as `error {notice}` (Retry card); the frontend never matches message text. *If wrong:* a 200 status for a config failure.
29. **R24** "Next review: …" counts an overdue concept as today. *If wrong:* wording.
30. **R42** Compose binds the API, website and database to 127.0.0.1 only (no auth until milestone 6). *If wrong:* reachable from this PC only (intended).

## Frontend and fidelity to the prototype

31. **R27** Fonts follow the prototype (Newsreader, IBM Plex Sans, IBM Plex Mono), not the PDF's Fraunces/Inter. *If wrong:* swap two `next/font` imports.
32. **R28** Routes are `/study/[id]` and `/progress/[id]`. *If wrong:* a folder rename.
33. **R30** The grade card leaves out the prototype's tide note ("stays at 52%"): the turn payload has no tide before/after. *If wrong:* a small visual difference.
34. **R31** The learner's answer sits inside its question card under "Your answer" (prototype), not as chat bubbles. *If wrong:* a layout change back.
35. **R32** Citation chips read like "[Notes p.12]", "[Notes § heading]" or "[domain]" (prototype), not "[1] [2]". *If wrong:* chip text only.
36. **R34** End session is a light button on the thread's first "Today" row, not sticky. *If wrong:* scroll up in a long session.
37. **R35** The website shows dates in the browser's time zone; server-written lines use `EBB_TIMEZONE`. *If wrong:* a weekday can differ near midnight when `EBB_TIMEZONE` is unset.
38. **R36** Tide band labels stay D7's "high tide / ebbing / low tide", not the prototype's "Strong / Steady / Fading". *If wrong:* three strings.
39. **R37** The streak grid shows 35 days like the prototype (API field `streak.days`). *If wrong:* rename back.
40. **R38** Review stages include the prototype's "Not started" column. *If wrong:* one column.

## Process

41. **R17** Six commits carried a "Claude Sonnet 5.5" trailer; a message-only rewrite fixed them before the merge (trees unchanged). *If wrong:* commit ids changed before merge.
42. **R39** The browser test uses a test-only tutor with non-repeating questions. *If wrong:* none.
