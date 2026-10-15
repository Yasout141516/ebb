# Ebb — Plan reviews

Amendments to [Ebb — Requirements & System Design](../Ebb%20—%20Requirements%20&%20System%20Design.pdf)
from three plan reviews (design, CEO, engineering) on 2026-10-15. The PDF is the
original plan; where this file disagrees with it, this file wins.

---

## 1. Design review

### Approved amendments

**D1. Exam timer end (FR-11).** Visual and screen-reader warnings at 5:00 and 1:00
(`aria-live="assertive"` announcement, timer turns to the warning colour). At 0:00
the exam auto-submits whatever is answered; unanswered questions count as incorrect,
matching the existing confirm-dialog copy. The results page notes "Submitted
automatically when time ran out."

**D2. Scanned / image-only PDFs (FR-1, FR-8).** If pypdf extracts no usable text
(fewer than ~50 characters per page on average), the upload row shows an error and
the file is not stored: "No text found — this looks like a scanned PDF. Export it
as text, or upload the PDF your slides were made from." No OCR in the MVP.

**D3. Split the mode chips (FR-2 to FR-11).** Quiz / Chat becomes a two-way
segmented toggle on the composer; both change the mode in place. Flashcards and
Exam are actions that open their own focused screens: "Review weak cards" and
"Take a 5-question exam" in the memory panel and on the end-of-round card (both
already exist in the prototype). One control group, one kind of behaviour.

**D4. Return loop — explicitly out of scope.** The MVP sends no reminders (no email,
no push). To keep the spaced-repetition promise honest, the session-end card and
the memory panel always show the next review: "Next review: Thursday · 3 concepts".
Listed under Unresolved for a later milestone.

**D5. Missing interaction states.**
- *Nothing due / caught up* — memory panel: "Tide's high. Next review Thursday."
  with a secondary "Quiz ahead anyway" action. Flashcards with no weak concepts
  shows the same message instead of an empty deck.
- *New-user dashboard* — with fewer than 3 days of attempts, hide the 30-day tide
  chart and show "Your tide chart fills in as you review — come back after 3
  sessions" with a single "Start a quiz" action. Streak and Due today still show.
- *Exam grading* — after submit: "Grading 5 answers…" with a per-question progress
  count; the results page loads when all grades are in. A per-question grading
  failure uses the existing `<ErrorCard>` pattern for that question only.
- *Magic-link sign-in* — "Check your inbox — we sent a link to you@…" with
  "Resend" (disabled for 30 s) and "Use a different email"; an expired link lands
  on "That link expired. Send a new one" with the email prefilled.
- *Upload limit* — at 5 of 5 files, "Add source" is disabled and reads
  "5 of 5 files"; the onboarding drop zone says the same.
- *Citations fail after 2 retries* — the answer is shown with the existing
  "General knowledge · not from your notes" label; a broken or empty citation chip
  is never rendered.

**D6. Mobile scope (NFR-15).** NFR-15 is amended: WCAG AA on every screen; the MVP
ships intentional 375 px layouts for Workspace and Dashboard only. Sign-in,
Onboarding, Flashcards, Exam and Exam results are desktop-first in the MVP and
listed under Unresolved.

**D7. Mastery vocabulary.** Two numbers exist and must not compete:
- **Tide %** — estimated chance of recall right now; decays daily, refills on
  review. *This is the headline everywhere* (memory panel, due list, dashboard).
- **Stage (0–5)** — the Leitner level; only changes on an answer and sets the next
  review gap. Shown only as small secondary dots in memory-panel concept rows:
  "●●●○○ level 3 · next gap 16 days".
- Labels (high tide / ebbing / low tide) are bands of tide %, never of stage.
- A one-line tooltip on each explains it. The tide formula itself is an
  engineering decision (see Engineering review).

### DESIGN REVIEW REPORT

| Pass | Before | After | Note |
|---|---|---|---|
| Information architecture | 7 | 9 | Mode controls split by behaviour (D3) |
| Interaction state coverage | 6 | 9 | Timer end, scanned PDFs, six missing states (D1, D2, D5) |
| User journey & emotional arc | 7 | 8 | Return loop explicitly scoped out; next review always visible (D4) |
| AI-slop risk | 9 | 9 | Tide metaphor and forgetting-curve sign-in give a real point of view |
| Design system alignment | 8 | 9 | One mastery vocabulary (D7); tokens and component map already strong |
| Responsive & accessibility | 7 | 7 | Claim narrowed to match reality (D6); timer announcements added (D1) |
| Unresolved decisions | 6 | 8 | Listed below instead of implied |

**Unresolved**
- Reminders (email digest or push) — deferred by choice; without them retention
  depends on learners remembering to return.
- 375 px layouts for Sign-in, Onboarding, Flashcards, Exam, Exam results.

**Verdict:** ready to build against for the MVP screens; the two deferrals are
conscious scope cuts, not gaps.

---

## 2. CEO review

### CEO REVIEW REPORT

**Mode:** deferred by the owner. Ebb is a portfolio project, not a commercial
product, so a strategy and scope review was skipped on 2026-10-15. No scope was
added or cut.

**Audit notes kept for later** (noted before the review was deferred):
1. Auth is built last (milestone 6), yet row-level security keyed on
   `auth.uid()` is the security model for every table. *Taken up in the
   engineering review.*
2. "Every table carries `user_id`" does not match the schema table. *Taken up in
   the engineering review.*
3. The success criterion is a reviewer using the app end to end, but the first
   screen asks for a Google account and leads to an empty onboarding. A seeded
   guest/demo subject would shorten time-to-value for a reviewer.
4. A public deploy on free LLM tiers has no per-user quota, and the plan does not
   say what happens when both Gemini and Groq are exhausted. *Taken up in the
   engineering review.*
5. FR-12 (Tavily/YouTube) and FR-13 (WeasyPrint PDF export) add three
   dependencies and a heavy native library for little agent-engineering signal;
   candidates to cut if time runs short.
6. The dispute → eval loop and the measured grading agreement rate are the
   project's strongest differentiator and are not surfaced anywhere a reviewer
   would see them.

**Biggest remaining risk:** a reviewer bounces at sign-in before seeing the agent
do anything (note 3).

**Verdict:** not assessed — deferred.

---

## 3. Engineering review

### Architecture as understood

Next.js (Vercel) talks only to FastAPI over HTTPS + Server-Sent Events, sending
the Supabase JWT. FastAPI verifies the JWT and runs the LangGraph agent
in-process: the quiz graph (`load_learner → pick_concept → retrieve_sources →
generate_questions → verify_citations → ask_question (interrupt) → grade_answer →
update_mastery → route_next → next concept | summarize | teach | next question`)
plus flashcard and exam subgraphs. Learning state is the source of truth in our
own tables (`concepts`, `attempts`); `PostgresSaver` checkpoints hold only the live
session, linked through `study_sessions.thread_id`. Retrieval is Postgres
full-text search over `document_chunks`. Only `generate_questions`,
`grade_answer` and `teach` call an LLM (Gemini, falling back to Groq).

### Issues and decisions

**E1. Per-user isolation (NFR-7) — decided: backend filter + RLS backstop.**
*Issue:* FastAPI and `PostgresSaver` connect to Postgres directly as a
privileged role, which bypasses row-level security, so `user_id = auth.uid()`
never runs for backend queries.
*Decision:* every repository function takes `user_id` from the verified JWT and
filters on it; no query is written without it. RLS policies stay enabled as a
backstop for any direct Supabase client access (e.g. Storage).
*Test:* pytest creates users A and B and asserts B gets 404 on A's subject,
concepts, documents, session and exam, on every read and write endpoint.
*Effort:* small.

**E2. Leitner promotion — decided: promote only when due.**
*Issue:* `update_mastery` moves a concept up on every correct answer, so five
correct answers in one session take it from stage 1 to stage 5 (35-day gap).
*Decision:* a correct answer promotes one stage only if `now >= next_review_at`;
otherwise the attempt is logged with `stage_after = stage_before`. Partial keeps
the stage and reschedules `next_review_at = now + interval[stage]` (otherwise it
stays due forever). Incorrect always returns to stage 1 and reschedules 1 day
out. The same rule applies to flashcard self-ratings.
*Test:* table-driven pytest over (stage, due/not due, correct/partial/incorrect).
*Effort:* trivial.

**E3. Double submit / concurrent resume — recommendation.**
*Issue:* a double-click or a second tab resumes the same interrupted thread
twice, writing two `attempts` and moving the stage twice.
*Recommendation:* the client sends a UUID `attempt_id` with each answer;
`attempts.id` is that UUID, so a retry hits the primary key and returns the
existing grade. Resumes on one thread are serialised with
`pg_advisory_xact_lock(hashtext(thread_id))`.
*Effort:* small.

**E4. Multiple-choice grading — decided: grade MCQ in code.**
*Issue:* MCQ correctness is a comparison, but the plan sends it through
`grade_answer`, costing an LLM call and adding non-determinism.
*Decision:* `generate_questions` returns, per MCQ option, `is_correct` and a
one-line `why` (stored in `questions.options` jsonb). MCQ grading is a lookup in
code; `grade_answer` runs only for short answers. MCQ grades cannot be disputed
(the Dispute button is hidden for them). LLM calls per MCQ cycle drop from about
3 to about 1 (plus `teach` on a miss).
*Effort:* small.

**E5. Tide formula — decided.**
`tide = 0.7 ** (days_since_last_review / interval[stage])` with intervals
`[1, 3, 7, 16, 35]` days for stages 1–5. Tide is 100% right after a review and
exactly 70% on the due date, so "due" (tide ≤ 70%) and `next_review_at <= now` are
the same condition. Stage 0 has no tide and displays as "new". Computed in one
pure function used by the API and the dashboard; never stored.
*Test:* pytest asserts 100% at t=0, 70% at t=interval, monotonic decay.
*Effort:* trivial.

**E6. LLM failure classes and quota — recommendation.**
*Issue:* no behaviour is specified when both providers are exhausted, a stream
fails after tokens were sent, or one visitor burns the shared free quota.
*Recommendation:* name the failure classes and handle each:

| Class | Trigger | Handler | User sees |
|---|---|---|---|
| `PrimaryUnavailable` | Gemini 429/5xx before first token | `.with_fallbacks()` to Groq | `<ModelNotice>` divider |
| `StreamInterrupted` | provider error after first token | no fallback mid-stream; turn marked failed, no stage change | `<ErrorCard>` with Retry; partial text kept, greyed |
| `AllProvidersExhausted` | Groq also fails | stop the turn, no stage change | "The tutor is out of breath for today — your progress is saved." |
| `OutputInvalid` | Pydantic validation fails | `.with_retry()` up to 2, counter in graph state | then `<ErrorCard>` |
| `UserQuotaExceeded` | user over daily cap | refuse before any LLM call | "Daily limit reached — reviews reset at midnight." |

A per-user daily cap (start at 40 LLM calls/day, tracked in a small
`llm_usage` counter table) keeps one visitor from taking the app down for
everyone. No bare `except Exception` in graph nodes.
*Effort:* small–medium.

**E7. Exam answers survive a closed tab — recommendation.**
*Issue:* answers live only in browser state until submit, and auto-submit (D1)
is enforced by a client timer.
*Recommendation:* add `answer_text` and `answered_at` to `exam_questions`; save
each answer as it is picked. The server is the clock: `exams.started_at +
duration_seconds` is the deadline, and any request after it (or the next page
load) auto-submits. Grading on submit runs short answers with bounded
concurrency (2 at a time) to stay under the free-tier requests-per-minute limit;
MCQs are instant (E4).
*Effort:* small.

**E8. Checkpointer connection and growth — recommendation.**
*Issue:* Supabase's transaction pooler (port 6543) breaks the prepared
statements psycopg uses in `PostgresSaver`; checkpoints also grow without bound
against the 500 MB free database.
*Recommendation:* connect the checkpointer through the session pooler (port 5432)
or set `prepare_threshold=None`. Delete checkpoint rows for threads whose
`study_sessions.ended_at` is older than 7 days, on app startup and via a daily
cron. Learning state is unaffected because it lives in our own tables.
*Effort:* small.

**E9. Schema and build order — recommendation.**
- Add `user_id uuid not null references auth.users` to every table, as the plan
  already states but the schema table omits. Add a check (or a trigger) that a
  child row's `user_id` matches its subject's.
- Keep the planned `concepts (user_id, next_review_at)` index and add
  `attempts (concept_id, graded_at)` for per-concept history.
- "Due today" compares `next_review_at` in the user's `profiles.timezone`.
- Build order: thread `user_id` through from milestone 2 with a fixed dev user,
  and add JWT verification at milestone 4 when FastAPI arrives, instead of
  retrofitting it at milestone 6. Milestone 6 keeps the Google OAuth UI and
  deployment.
*Effort:* small.

**E10. Tests — recommendation.** Beyond Leitner and the citation verifier,
cover the highest-risk paths: cross-user isolation (E1), idempotent resume (E3),
`route_next` for each of its four outcomes, and the full quiz graph run
end-to-end with `langchain_core`'s `GenericFakeChatModel` standing in for
Gemini, so graph tests need no network or quota.

### Still open

- **Backend host.** Cloud Run (cold start of a few seconds, needs a billing
  account on file) vs. Render free (no card, roughly 30–60 s wake-up after
  15 minutes idle). The "about 20 seconds" cold-start copy in the prototype must
  match whichever is chosen, measured rather than guessed.
- Chunking parameters for `document_chunks` (target size, overlap, splitting on
  headings vs. pages) — pick during milestone 3 and record here.

### ENG REVIEW REPORT

- **Resolved in review:** isolation model (E1), promotion rule (E2), MCQ grading
  (E4), tide formula (E5).
- **Recommended, to apply while building:** idempotent resume (E3), named LLM
  failure classes and per-user cap (E6), server-side exam state (E7),
  checkpointer pooling and pruning (E8), schema and build order (E9), tests (E10).
- **Open:** backend host; chunking parameters.

**Verdict:** safe to start building milestones 1–3 now. Pin down the backend host
before milestone 6, and land E1 and E9 before any data from a second user exists.
