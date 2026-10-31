# Pre-milestone-5 — rulings made on the owner's behalf

Decisions taken during the build of shuffled options, the LLM gateway, the Costs page and the
learn phase (subagent-driven development, 2026-10-27 to 2026-10-31) where the spec or plan left a
choice open or conflicted. Each line: the decision, why, then what it costs if it turns out wrong.
Owner's own decisions are in the
[design](specs/2026-10-27-learn-phase-gateway-costs-design.md) and [HANDOFF.md](../HANDOFF.md).
Earlier rulings: [m4-rulings.md](m4-rulings.md), [m3-rulings.md](m3-rulings.md). R-numbers are
this build's own (they restart at R1).


## Gateway and providers

1. **R1** In Task 2, `LangChainTutor.__init__(chains: dict[Role, list[Provider]] | list[Provider], attempts=3, rng=None)`; a plain list means every role uses that chain (keeps existing tests simple). `_run` gains a `role` argument and each method passes its role from the Global Constraints table (endpoints wait for Task 4). — Spec goal 3 says changing a role's model routes that work; doing it in T2 makes T2's deliverable real. *If wrong:* T4 re-touches the same lines.
2. **R4** missing-key copy is `"{KEY} is not set — {setting} uses {Provider}"` for every provider; Groq appends " (get one free at console.groq.com)". — Global Constraints extend the spec's shape to GROQ; the free hint keeps the old message's help. *If wrong:* wording only. (Supersedes my dispatch note to keep legacy Groq wording.)
3. **R6** `StoreRecorder(store, user_id)` records under the request scope's user when present, else its own user. — LlmCall.user_id is required (R5) and calls outside a scope (before Task 5, CLI setup) must still be recorded. *If wrong:* misattributed rows in a one-user app.
4. **R7** keep `LangChainTutor(chains, attempts=3, rng=None)` positional order; `recorder` is keyword-only-ish with NullRecorder default (plan put recorder second). — Avoids breaking every existing positional call. *If wrong:* signature tweak.
5. **R8** `LlmCall.backup` = attempt on chain index > 0. — How "fell back" is recorded for T6. *If wrong:* one column's meaning.
6. **R9** prices are keyed by (provider, model) — `prices.cost(provider, model, input_tokens, output_tokens)`, `PRICES[(provider, model)]`. — The same model name on OpenRouter vs Groq has different prices; Tasks 6–8 consume it, so fix before them. *If wrong:* one key change. (Goes into Task 4's fix round.)
7. **R10** no Mistral price recorded until verified on Mistral's own page — anti-goal "a dashboard that lies". *If wrong:* eval-writer costs show "price unknown".
8. **R11** once text has reached `on_text` in a call, a failing attempt is neither retried nor sent to the backup — it raises; before any text, retries/backup are silent. — Owner's decision in the spec (wipe + Retry, nothing rewritten on screen). *If wrong:* fewer automatic recoveries for streamed lessons.

## Cost records and the Costs numbers

1. **R2** Task 3 adds `backup boolean not null default false` to `llm_calls` and `LlmCall.backup: bool` (true when the attempt used a role's backup model). — T4/T6 need it to show fallbacks. *If wrong:* one unused column.
2. **R3** `Store.llm_calls(user_id: UUID, since, until, all_tenants: bool = False)`; `user_id` stays first and filters unless `all_tenants=True`, used only by the dev Costs page. — Keeps the E1 rule's shape while allowing the per-tenant view the spec asks for. *If wrong:* one parameter rename.
3. **R5** `LlmCall.user_id: UUID` (after request_id); `llm_calls` returns it; `record_llm_call(user_id, call)` raises ValueError on mismatch. — Task 6 per-tenant totals need it; one source of truth. *If wrong:* redundant field.
4. **R12** `costs.role_health(calls, roles: Mapping[Role, Sequence[tuple[str, str]]])` takes each role's chain as (provider, model) pairs, not LangChain Provider objects. — Keeps costs.py pure and testable; Task 7 builds the mapping from `settings.role_models()`. *If wrong:* one parameter shape.
5. **R13** `per_day` and day anomalies count tokens as input + output (reasoning already inside output per prices.py); `by_feature` returns the six features in the plan's order, zero-filled. — Spec says "tokens per day"; one consistent definition. *If wrong:* chart totals differ.
6. **R14** "retried" = some provider was asked more than once in the call (attempt > 1); a main-fail-then-backup call is "fell back", not "retried". — My R12-era wording "more than one attempt on any provider" meant this; keeps the two rates distinct. *If wrong:* retry rate reads lower. (Reviewer finding I2 closed as not-a-defect.)
7. **R15** anomaly history = llm_calls over the 30 days before `since`, same tenant filter. — Simple at one user's volume; costs.anomalies picks prev-50/7-day itself. *If wrong:* thinner baselines for rarely used endpoints.
8. **R16** query params range ∈ 24h|7d|30d (default 7d, else 422); tenant UUID or omitted = all tenants; endpoint/model exact filters before aggregation; until = now, days in services.tz. — Plan said only "filters as query parameters". *If wrong:* param shape.
9. **R17** roles in the summary come from settings.role_models() as (provider, model) pairs so configured roles show with no calls. *If wrong:* roles appear only after use.
10. **R21** add `Store.ai_calls_used(user_id, day)` and `/costs/summary.daily_calls {used, limit}` for the profile user, today in services.tz, unfiltered. — Spec lists the card; small backend addition in the fix round. *If wrong:* one field.

## Learn phase

1. **R23** Task 9 — Streamer is the gateway's on_text only when the caller passes one; parse only the LAST <<<EBB…EBB>>> block; checks live only in the trailer (UI renders them), not in the lesson body; FakeTutor gets a scripted fail-after-streaming switch for Task 11. — Keeps the trailer hidden and injection-proof, and lets T11 test wipe+Retry. *If wrong:* small interface changes in T11/T14.
2. **R24** Task 10 — add_exchange on an existing position is a no-op; if concept_lessons lacks a checks column, add migration 006 (never edit 005). — Learn turns may be retried; 005 may already be applied somewhere. *If wrong:* one extra migration.
3. **R25** Task 11 — discard decided by the service's own "did a TextDelta reach the reader" tracking; session_lock per learn thread; one request scope per learn turn; ask_id dedupe in state; no-checkpoint resume rebuilds from stored lesson + exchanges; store the first call's model. — From T9 review carry-notes and the spec's anti-goals. *If wrong:* event-shape tweaks in T13/T14.
4. **R26** a cut-off answer is finished only on a retry of the same ask_id; start / Ready / a different ask drop the pending ask back to wait (no model call); a cut-off write_lesson still resumes on start. — Spec: coming back shows the stored lesson with no model call; Retry is the learner's explicit choice. *If wrong:* an abandoned question is lost unless re-asked.
5. **R27** one start/reply yields exactly one final LearnTurn (or Learned); intermediate rebuilt turns are not yielded. — Simple end-of-turn contract for T13/T14. *If wrong:* T13 handles several turns.
6. **R30** Task 13 — web never sends {"skip": true}; answer posted to a learn turn → 422; LearnNotStarted → 409 "Start the lesson first."; SSE per call ends with exactly one learn|learned; citations in the quiz's dict shape. — From T11/T12 carry notes. *If wrong:* status codes / event names.

## Quiz hook (what happens after the lesson)

1. **R28** Task 12 — learned/skip replies are resend-safe via _applied; quit on a learn turn ends like quit on a question; MCQ-first applies only to the first question after a learn turn; due reviews and answered/learned concepts never get a lesson. — Spec + resend safety. *If wrong:* small routing tweaks.
2. **R29** the learn payload also carries the previous answer's `feedback`, `lesson`, `notices`, `can_dispute`, `dispute_result` exactly as a question payload does; a dispute reply on a learn turn goes through regrade and returns to the learn turn. — Spec turn order shows the previous grade card first; hiding it behind the next lesson breaks that. *If wrong:* extra payload keys.

## Web UI

1. **R18** header shows Costs + "dev only" only when /health says costs: true; reuse the existing health load if reachable, else one fetch in the header. — Spec: link while EBB_SHOW_COSTS is on. *If wrong:* one extra request.
2. **R19** money `$` with enough decimals to be non-zero, null → "price unknown"; tokens with separators, null → "not reported"; times `840 ms` / `2.9 s`, null → "—"; helpers in one tested module. — Spec copy + anti-goal. *If wrong:* formatting only.
3. **R20** filters are controlled selects refetching summary+calls; endpoint/model/tenant options from the current range's data; default 7d. *If wrong:* option lists.
4. **R22** (amends R20) filter options come from a range-only summary load refetched on range change. — Lets users switch endpoint→endpoint directly. *If wrong:* one extra request per range change.
5. **R31** Task 14 — replace streamed text with payload markdown on the final learn event; discard clears then ErrorCard Retry with the same ask_id; Read the lesson again is a left-column link per learned concept; e2e fake adjusted only to keep the existing spec green. — From T9/T13 carry notes. *If wrong:* UI tweaks.
6. **R32** drop `skipHtml` so raw HTML renders as escaped text (react-markdown escapes HTML without rehype-raw) — matches Review Focus 4 "shown as text" and keeps lessons about HTML intact. *If wrong:* one prop.
7. **R33** add `learned: bool` to the concepts API (`ConceptState`, from store.is_learned or a batched query) and `has_notes` to the learn payload; the left column uses `learned` instead of probing each concept. — Removes N requests + 404 noise; links for learned-but-unanswered concepts; correct "Not in your notes". *If wrong:* small backend additions.

## Deferred

Minor findings from reviews that are still open (none blocks the work). Items a later task fixed are left out (for example the gateway marking a row `ok` when `parse` then raised, closed in Task 9; the `Health` type missing `costs`). Two can affect the real app, and are marked so.

- Task 1: `rng or random.Random()` → explicit `is not None` (tutor.py ~427)
- Task 1: FakeTutor rng path has no direct unit test (covered by e2e)
- Task 2: role_models() parses every role even when unused (a bad writer value breaks `ebb study`)
- Task 3: `(user_id = %s or %s)` may defeat the (user_id, at) index — branch SQL
- Task 3: `asdict(call)` per insert; lesson_exchanges has no user_id index; test_fakes.py untouched (coverage in test_db.py)
- Task 4: `ebb eval build/run` print a DB failure line when the DB is down; `eval build` recorder choice untested
- Task 4: no test that cli/api wire StoreRecorder for learner-facing commands
- Task 4: live smoke check that Groq streams usage under x_groq (else rows honestly record None)
- Task 4: test_tutor.py ~34 s because retry tests really sleep (inject no-op sleep in those tests)
- Task 5: no test that GeneratorExit drain keeps the turn's id; drafts test's main-thread `current()==(None,None)` proves nothing; generate not asserted; dead `thread_id="unset"`
- Task 6: role_health counts invalid attempts as failed while failures() drops recovered invalid ones; chain[0] IndexError on empty chain
- Task 6: DayTokens fields are int | None now; a call over 3× on both counts yields two token anomalies (UI may show two rows for one call); baselines take previous 50 ok calls then filter to known values
- Task 7: /costs/calls tenant/endpoint/model filters and tenant-filtered anomalies untested; retry+fallback rows of one call repeat the same anomaly message; /costs/calls loads the whole range then slices
- Task 8: header's own /health fetch = one extra request per full-header page
- Task 8: first load fetches the same /costs/summary URL twice (main + options)
- Task 8: daily-limit card hidden on an empty range (inside calls > 0 branch); non-UTC tz untested for daily_calls
- Task 9: an unclosed real code block right before the marker loses its dangling fence line; tilde/indented fences not recognised
- Task 10: no committed test that 006 applies on top of existing lesson rows; MemoryStore save_lesson/add_exchange don't check concept ownership (Pg FK does)
- Task 11: ask_id dedupe doesn't survive a lost checkpoint (Exchange has no ask_id column)
- Task 11: read_back raises bare LookupError if the lesson is missing during a drop (unreachable)
- Task 12: resend of a Ready that itself ended the session → SessionNotFound, not the summary
- Task 13: no ValueError→422 handler (schema makes it unreachable); eager psycopg.Error in start/reply → plain 500 not the DATABASE_DOWN notice event; no reader-gone / repeated-ask_id API tests; LessonPage typed dict[str, Any]; _respond docstring narrow **Real-app behaviour (a database outage during start/reply).**
- Task 12: latest-answer tie-break uses random uuid id (repo.py ~379, fakes.py ~208); two answers in the same microsecond could refuse a legit dispute — consider an insertion sequence. Real-app risk low. **Real-app behaviour.**
- Task 14: notice de-dup across the whole learn phase; answer step label shows under Ready; GFM footnote back-links lose links
- Task 14: a quick 4xx on the auto {learned:true} hand-off leaves handed set (no resend except Retry); GET lesson does an extra get_concept for subject_id
- Task 14/15: learn checkpoints (`learn:{user}:{concept}`) are never pruned unless the concept is learned (`threads_to_prune` only covers study sessions); harmless at one per concept, for milestone 6
- Task 6: flooring still prints "3.0 times" for a call at e.g. 3.007× — consider "just over 3 times" wording or two decimals near the threshold (my requested fix was insufficient)

## Live smoke run (2026-10-31, real Groq / Mistral / Tavily keys)

Worked live: streamed question writing and rewriting, re-teaching, a lesson (Tavily search plus
notes, 1,553 streamed pieces, first text at 6.6 s, 10.9 s in all), a learn answer, Ready, the
MCQ after Ready, shuffled options, and the Costs page and API. Groq does report usage on streamed
calls: input, output and reasoning tokens, TTFT and cost were all recorded.

- **Fixed (6b464fb):** gpt-oss once ended a good learn answer with the bare trailer object and
  no `<<<EBB` markers. The parse failed and the answer was wiped behind the error card. `split`
  now reads a final JSON object made only of the trailer's keys.
- **Not exercised:** free-text grading. The session picked MCQs every time for these review
  concepts. Grading goes through the same gateway path, which ran live for every other endpoint.
- **Found, not fixed (for the token-saving and lag work):**
  - Groq's free tier for gpt-oss-120b allows 8,000 tokens a minute. One lesson (about 7.4k)
    uses almost all of it, so a learn question asked in the same minute gets a 429.
  - Each learn answer resends the whole lesson, about 6.5k input tokens, so the free tier allows
    roughly one learn question a minute.
  - The Mistral backup key answered 429 "rate limited" throughout, so the backup didn't help.
  - Each provider client also retries once inside its SDK (`max_retries=1`), on top of the
    gateway's retries, so one 429 attempt took 10.4 s. A rate-limited turn took 36 s before the
    gateway kept the first draft of the questions.
