# Milestone 4: The web app — FastAPI backend and Next.js frontend (design)

Date: 2026-10-23 · Status: awaiting owner approval

Builds milestone 4 of [Ebb — Requirements & System Design](../../../Ebb%20—%20Requirements%20&%20System%20Design.pdf):
"FastAPI backend and the Next.js frontend from the prototype, including the memory panel and
dashboard". It builds on milestone 3 ([spec](2026-10-18-uploads-citations-disputes-design.md))
and takes in the "before the milestone 4 API" items from
[m1-followups.md](../m1-followups.md), [m2-followups.md](../m2-followups.md) and
[m3-followups.md](../m3-followups.md), design decisions D3–D7 and engineering decisions E3, E6
and E9 from [plan-reviews.md](../../plan-reviews.md). The screens come from
[ebb-prototype.html](../../../ebb-prototype.html). Where this document disagrees with the PDF
or the prototype, this document wins.

## Intent

Ebb works today, but only in a terminal. Milestone 4 gives it its real face: the web app the
prototype shows. The owner opens `localhost:3000`, adds a subject from a topic or from their
lecture notes, studies in the workspace, where questions, cited grades and lessons appear as
cards beside a memory panel of tide gauges, and checks the progress dashboard. The owner will
test milestone 3's features (notes, citations, web questions, disputes) hands-on for the first
time through this app, so it has to expose all of them.

The project is a portfolio piece about agent engineering. The agent itself doesn't change
much in this milestone. The work is wrapping the LangGraph graph in an API that streams its
progress, and building a frontend that renders every state the graph can be in. The graph
gets cleaned up first, because the API will depend on its shape.

## Goals

1. **One command.** `docker compose up` starts the database, the API and the website.
   Ebb is then at `http://localhost:3000`, on the owner's PC only.
2. **Onboarding.** Create a subject from a typed topic or from up to 5 notes files, with
   per-file progress, the found concepts, every refusal shown on the file's row, and the
   privacy notice.
3. **Study workspace.** Run a full quiz session in the browser. Multiple-choice and short
   answers, grade cards with citation chips and quotes, lessons, notices, disputes,
   resuming an unfinished session, and an end-of-session summary. Memory panel beside it.
4. **Add notes later.** Add a file to an existing subject (the web version of `ebb add`).
5. **Progress dashboard.** Study streak, due today, average tide, a 30-day tide chart per
   concept and the review stages, one subject at a time.
6. **Live progress (NFR-1, amended).** While the tutor works, the screen shows which step
   it is on, within 2 seconds of the click.
7. **The CLI keeps working** against the same database, at the same time as the website.
8. **Hardening before the API wraps the graph.** The deferred graph clean-ups, the
   correctness fixes a long-running server needs, a daily AI-call cap and the safety items.

## Non-goals (later milestones or out of scope)

- Sign-in, JWT verification, Supabase (milestone 6). See *Plan amendments*.
- Deploying anywhere, the cold-start "Waking up the tutor" state (NFR-14, milestone 6).
- Chat mode. **Dropped by the owner**, not deferred.
- Flashcards, exam mode, PDF export, suggested-reading links (milestone 5).
- Word-by-word streaming of model text (owner's decision, see *Streaming*).
- Removing or replacing an uploaded file; deleting or renaming a subject (as in milestone 3).
- Keeping the original uploaded files. Only extracted text and chunks are stored (owner's
  decision: no "Open page N in notes" link).
- Mistral. The owner runs Groq only until the Mistral account has an active plan
  (2026-10-22). The code keeps its optional Mistral fallback as it is; nothing in this
  milestone depends on it.
- Reminders (D4), the grading-eval improvements, terminal polish.

## Anti-goals (failures even if everything "works")

- **The browser and the CLI corrupting each other's progress**: an answer counted twice, a
  stage moved twice, a dispute applied to the wrong answer.
- **A screen that lies.** A citation chip with no quote, a grade shown before it is saved,
  or text that appears and is then rewritten.
- **A frozen screen.** A click that shows nothing for more than 2 seconds while the tutor
  works.
- **Losing the owner's data.** The first run on `main` migrates the real `ebb` database to
  `003`; it must be backed up first (see *Rollout*).
- **The API deciding things the graph decides.** Routing, scheduling and grading stay in
  the graph and the pure modules. The API only carries replies in and payloads out.
- **Logic copied between the CLI and the API.** Starting, resuming and driving a session
  must be one shared piece of code.

## Decisions made by the owner

| Topic | Decision |
|---|---|
| Where it runs | Only on the owner's PC: `docker compose up` → database, API, website on `localhost:3000`. No online deploy until sign-in (M6) |
| Screens | Onboarding (adding a subject or notes matters most), the study workspace (Quiz) and the progress dashboard |
| Chat mode | Dropped |
| Flashcards / Exam | Stay in milestone 5 |
| Sign-in | Milestone 6 |
| Styling | Port the prototype's CSS (variables and styles) into CSS Modules. No Tailwind, no shadcn |
| Citation popover | No "Open page N in notes" link (files aren't stored) |
| Creating a subject from notes | All-or-nothing: each file is read and its concepts found as it is added, but nothing is saved until **Start studying** |
| Coming back to an unfinished session | Ask, like the CLI: "You stopped on DNS resolution, question 2, 3 hours ago" with **Resume** / **Start fresh** |
| Dashboard and several subjects | One subject at a time, with a subject picker. The streak counts study in any subject |
| Streaming (NFR-1) | Live step events over Server-Sent Events; each card appears whole, with its verified citations. No word-by-word text |
| Backlog in this milestone | All four groups: the graph clean-ups (first), the correctness fixes, the daily AI-call cap, the safety items |
| Providers | Groq only for now; Mistral abandoned until its account is active |

**Facts from the code these rest on (verified, not assumed):**
- One fixed dev user already exists: `EBB_DEV_USER_ID`, default
  `00000000-0000-0000-0000-000000000001` ([config.py](../../../backend/src/ebb/config.py)).
  The website uses the same user as the CLI.
- Locks are per study session (`thread_id`), not per subject, and every grade is applied to
  the concept's locked database row ([repo.py](../../../backend/src/ebb/db/repo.py),
  `record_answer`). So a browser session and an `ebb study` on the same subject can run at
  once without lost updates.
- Each graph turn already reaches the caller as one `interrupt` payload: concept, question
  number, question, feedback (grade and citations), lesson and its citations, notices,
  `can_dispute` and `dispute_result` ([nodes.py](../../../backend/src/ebb/graph/nodes.py),
  `ask_question`). The frontend renders that payload; the CLI's `render_turn` is the
  reference for what each part means.
- Every LLM call returns structured JSON (`with_structured_output`), and citations are
  verified in code after the whole response arrives.

## Plan amendments

- **E9, "add JWT verification at milestone 4", moves to milestone 6.** With one user on
  localhost there is no token to verify. The API still takes the user id from one place (the
  dev-user setting today, the verified JWT in milestone 6), and every store call keeps taking
  `user_id` first (E1).
- **NFR-1** changes from "first token in about 2 s, token streaming in LangGraph's messages
  mode" to "first visible progress within 2 s: a Server-Sent Event names each step as it
  starts; each card appears complete".
- **D3** (Quiz/Chat toggle, Flashcards/Exam actions): Chat is dropped, so the composer has
  no mode toggle in milestone 4. Flashcards and Exam actions are hidden until milestone 5.
- **NFR-15 and the tech stack**: "shadcn/ui on Radix" and "Tailwind" are replaced by CSS
  Modules ported from the prototype. Accessibility stays WCAG AA. D6's 375 px layouts for
  the Workspace and the Dashboard still apply.
- The PDF's Gemini references are stale (Groq primary since 2026-10-21, see plan-reviews).

## Behaviour

### Layout and shared pieces

- The header shows the Ebb mark, **Study** and **Progress** links, a status pill ("Tutor
  ready", or "Tutor ready · backup model" after a fallback) and the light/dark toggle from
  the prototype. *(My call)* No avatar or initials: there is no signed-in person yet.
- *(My call)* The theme choice is remembered in the browser.
- Fonts: Fraunces for headings, Inter for the interface (both free on Google Fonts).
- Dates and days are shown in the profile's time zone (`profiles.timezone`), resolved the
  same way as the CLI does today (`EBB_TIMEZONE`, else the machine's, else UTC). Inside
  Docker that means `EBB_TIMEZONE` should be set; the README says so.
- With no subjects, every page leads to onboarding (the prototype's "No subjects yet"
  empty state).

### Onboarding — a new subject

The prototype's screen "What are you studying?", with two tabs, **Upload notes** (default)
and **Type a topic**, a **Subject name** field and **Start studying**.

**Type a topic.** A topic field with the "Try:" suggestions. The topic is the subject's
name, as in `ebb new "<topic>"`; there is no separate name field on this tab. **Start
studying** shows "Mapping the topic…", makes the existing `extract_concepts` call (6–10
concepts), saves the subject and opens the workspace.

**Upload notes.** *(My call)* The **Subject name** comes first, above the drop zone, because
the concept call needs it (`concepts_from_notes.md` starts with `Subject: $subject`), just
as `ebb new "<topic>" --notes` needs the topic before the files. The drop zone is enabled
once the name is filled in and isn't already taken. The name is fixed once the first file
is being read; changing it means **Start over** (discarding the draft), so concepts are
never found under one name and saved under another. Then a drop zone and **Choose file**,
the limits line ("PDF, TXT or MD · up to
20 MB per file · 5 files per subject") and the privacy notice ("Don't upload sensitive
documents. Files are processed by an AI model to write questions and cite answers.").
Each added file gets a row that goes through:
1. **Uploading**: a progress bar.
2. **Reading**: the same checks and extraction as `ebb new --notes` (type, size, scanned
   PDF, UTF-8, empty, duplicate of another file in this draft).
3. **Finding concepts**: the existing `concepts_from_notes` call. Files are processed one at
   a time, in the order added, and each call sees the concepts found in the earlier files,
   exactly as the CLI does, so no duplicates appear across files.
4. **Ready**: "38 pages · 112 passages", then "8 concepts found:" with the names.

A refusal shows on that file's row with the CLI's exact message (e.g. "26.4 MB is over the
20 MB limit" + "Split it into chapters, or upload just the pages you're studying.", or D2's
scanned-PDF message), and the file is not part of the draft. At 5 files the drop zone reads
"5 of 5 files" and is disabled (D5). If the concept call fails (`AllProvidersExhausted`),
the row shows the exhausted notice with **Try again**.

**All-or-nothing (owner's decision).** Nothing is saved until **Start studying**. It is
enabled when at least one file is ready and none is still working. It then saves the subject, every ready file and every concept in one transaction
(`create_subject_with_notes`) and opens the workspace. *(My call)* The draft lives in the API
process's memory for up to an hour; leaving the page, or restarting the API, discards it.
*(My call)* A ready file can be removed from the draft with an ✕ before **Start studying**;
the concepts of later files are not recomputed (a removed file's concepts that a later file
matched are simply kept off the list, as `merge_note_concepts` decides).

**Names.** A name that already exists (ignoring case) is refused inline, on either tab:
"Computer Networks already exists — add notes to it from its Sources list." *(My call)* The
name field starts empty, not guessed from a file name. The name is checked again when the
draft is committed (another tab or the CLI may have taken it meanwhile).

### Adding notes to an existing subject

The workspace's **Sources** list shows each file ("CSE321_Wk1-6.pdf · 38 pages · 4.2 MB")
and an **Add source** button. It opens the same upload screen in "add to <subject>" mode:
one file at a time, saved as soon as its concepts are found, which is what `ebb add` does.
The report shows "9 concepts in these notes — 2 already here (Subnetting, DNS), 7 new" with
the new names. At 5 of 5 the button is disabled and reads "5 of 5 files" (D5). With no
sources, the prototype's empty state shows ("Grades will be marked 'General knowledge'
until you add notes").

### The study workspace

Three columns as in the prototype: **Subjects** and **Sources** on the left, the tutor
thread in the middle, the **Memory** panel on the right. On a 375 px screen the memory panel
becomes a bottom sheet (D6).

**Starting.** Opening a subject's workspace:
- If the subject has a resumable session (open, active within 24 hours), the thread shows a
  card: "You stopped on DNS resolution, question 2, 3 hours ago." with **Resume** and
  **Start fresh**. Resume continues at the same question; a session stopped mid-step
  (e.g. while grading) finishes that step first, as `ebb study` does. Start fresh ends the
  old session and starts a new one. These are the CLI's rules, from one shared piece of code.
- If that session is being driven elsewhere (another tab or a terminal), the card says
  "That session is open in another tab or terminal." and offers only **Start fresh**.
- *(My call)* **The same tab coming back** (a page refresh, or Study → Progress → Study)
  is not "elsewhere" and doesn't see the Resume card: the tab keeps its own thread of cards
  for that session in browser session storage and reconnects to the current question
  silently. Its own lease never refuses it. Any other way back (a closed tab, a new tab,
  another browser) shows the Resume card, and the thread then starts from the current turn
  only, as a resumed `ebb study` does. Earlier cards aren't kept on the server: the
  checkpoint holds only the current turn, and storing a session's history is not worth a
  new table.
- Otherwise a new session starts with the CLI's greeting ("Welcome back — 3 ideas are
  ebbing. Reviewing those first." and the other variants).

**A turn.** The middle column is a thread of cards, oldest at the top. Each graph turn
appends, in this order:
1. The **grade card** for the previous answer: "● Correct", "◐ Partial" or "○ Incorrect",
   the explanation, then **Sources** as numbered chips ([1], [2]…), numbered continuously
   across "From your notes" and "From the web". A chip opens a popover with the label (e.g.
   "CSE321_Wk1-6.pdf · p. 12", "networks.md · § Subnet masks", "cloudflare.com · page
   title") and the quoted sentence, with no "Open page" link. If the subject has notes and
   the grade has no valid citation: "General knowledge · not from your notes". Never an
   empty or broken chip.
2. The **lesson** after a miss, titled "Let's fix that", with its own citation chips, if it
   has any.
3. **Notices** as inline dividers, e.g. "Switched to a backup model while the main one is
   busy." or "Web search unavailable — skipped repeated questions."
4. The **question card**: "Multiple choice · Subnetting · Q2" or "Short answer · …", the
   prompt, and the source line under it ("From your notes · p. 12", "From the web ·
   cloudflare.com"). Multiple choice: four radio options, keys **1**–**4** to choose,
   **Enter** or **Check answer** to submit. Short answer: typed in the composer at the
   bottom, **Enter** to send (Shift+Enter for a new line).

The composer has no mode chips (Chat is dropped; Flashcards/Exam are hidden). It is enabled
only while a short-answer question is waiting. *(My call)* Every answer is shown in the
thread as the learner's own bubble.

**Disputes.** **Dispute grade** appears on the latest grade card only when the payload says
it can be disputed (`can_dispute`): a short answer graded by the model, in this session,
before the next answer. It opens the prototype's dialog: "What's wrong with it?" with the
four existing reasons ("My answer is correct", "The question is ambiguous", "My notes say
something different", "Something else"), "Explain (optional)", **Cancel** and **Send for
re-check**. While the re-check runs, the button is replaced by "Dispute sent. A second
grader is re-checking your answer…". The result appears on that card as "Re-check
complete." with the CLI's wording ("Re-check: raised to Correct — Subnetting back to ●●●○○
level 3." and the others), plus any citations, and the question is shown again, as today.

**Ending.** An **End session** button in the thread's header does what `q` does: it ends
the session (not resumable) and shows the summary. When the tutor ends the session itself
(everything learned and nothing new to add), the same summary appears, with "You're all
caught up — next review Thu 30 Oct." The **summary card** lists each concept studied: answers
(✓ ◐ ✗ counts), tide before → after, stage dots before → after, and next review, plus D4's
line: "Next review: Thursday · 3 concepts". With no answers: "No answers this session. Your
memory levels are unchanged." Closing the tab is not an ending: the session stays resumable
for 24 hours, like a closed terminal.

**Switching subjects** in the left column while a session is open leaves it open and
resumable. *(My call)*

**Memory panel** (right), always for the current subject and refreshed after every graded
answer:
- "Due for review · N": each due concept with "Tide 64% · due 2 days ago". *(My call)* No
  per-concept Review button (it led to Flashcards).
- "Nothing due" state (D5): "Tide's high. Next review Thursday." *(My call)* without D5's
  "Quiz ahead anyway" action, because quizzing concepts that aren't due is still a non-goal
  (milestone 2).
- "All concepts": name, tide % (headline) with its band label (high tide / ebbing / low
  tide), and the stage as small dots, "●●●○○ level 3 · next gap 16 days" (D7). New concepts
  show "new". One-line tooltips explain tide and stage (D7).
- "Where the tide will be in 3 days without review": a faint marker per concept, computed
  with the tide formula.

**Hidden until milestone 5:** the Flashcards and Exam chips, "Review weak cards", "Take a
5-question exam", and the Suggested-reading card. Hidden, not greyed out. *(My call)*

**Errors in the workspace.**
- A tutor failure (`AllProvidersExhausted`) uses the prototype's error card: "The tutor
  couldn't respond — your progress is saved. Try again in a minute." with **Retry**. For a
  failed grade the same question comes back, as today.
- A configuration error (bad or missing `GROQ_API_KEY`, a retired model) shows one
  full-width message with the same text as the CLI's exit-2 line.
- Database unreachable: "Can't reach the database. Is it running? Try: docker compose up -d".
- The daily cap and refusals: see *Daily AI-call cap* and *Safety*.

### Streaming (NFR-1, amended)

While the graph runs between two questions, the API sends Server-Sent Events: one event when
each step starts, then the turn's payload when it is ready. The thread shows the current step
in the prototype's streaming row ("Ebb · Checking against your notes…") until the cards
appear. Step labels:

| Graph node | Label |
|---|---|
| `retrieve_notes` | Reading your notes… |
| `generate_questions` | Writing questions… (web mode: Writing questions from the web…) |
| `verify_citations` | Checking citations against your notes… |
| `search_web` | Searching the web… |
| `grade_answer` | Grading against your notes… (no notes: Grading…) |
| `teach` | Writing a short lesson… |
| `regrade` | Re-checking… |
| `extend_subject` | Finding more advanced concepts… |
| `pick_concept`, `load_learner`, `update_mastery` and the other code steps | no label of their own (they take milliseconds) |

The first event must reach the browser within 2 seconds of the click. Cards appear complete,
with verified citations; text is never rewritten on screen. *(My call)* No "Stop generating"
button: with whole cards there is nothing partial to stop.

Onboarding uses the same mechanism for each file's row (Uploading → Reading → Finding
concepts → Ready).

### Progress dashboard

The prototype's "Progress" page for one subject, with a subject picker at the top (owner's
decision) and **Start today's review** (opens the workspace).

- **Study streak**: consecutive days, in the profile's time zone, with at least one answer
  that wasn't voided, in **any** subject (owner's decision); today counts once there is an
  answer, and an unbroken run up to yesterday still shows. "Best: N days" is the longest run
  ever. The prototype's day dots show the last 14 days.
- **Due today**: the subject's due concepts with their tide %, and **Review these now**.
  *(My call)* The "about 9 minutes" estimate is left out: nothing defines it.
- **Average tide**: the mean tide of the subject's studied concepts (stage ≥ 1), with the
  change since 30 days ago ("+9 points since 23 Sep"), and the prototype's footnote naming
  how many concepts are counted and which aren't yet.
- **Tide levels, past 30 days**: one line per studied concept, rising at each review, with
  the next 3 days without review dashed. Picking a concept enlarges its line. Never-studied
  concepts show "Not studied yet. It joins the queue after its first quiz."
- **Review stages**: columns for stages 1–5 with each concept's next review, and the
  prototype's one-line explanation; empty stages say "Nothing here yet."
- **New-user state (D5)**: with fewer than 3 days of answers, the chart is hidden and the
  page shows "Your tide chart fills in as you review — come back after 3 sessions" with a
  single **Start a quiz** action. Streak and Due today still show.

**How the history is computed.** Nothing new is stored. A concept's past states come from
replaying its answers that weren't voided, oldest first by `graded_at`, through the pure
`mastery.apply_grade` from stage 0. Disputes have already rewritten a changed answer's
grade, and a voided answer is skipped, so the replay gives the same states the database
went through. Tide at any moment then follows from the tide formula. On data written only
by Ebb, the replay ends at the concept's stored state; a test pins this.

*(My call)* **The stored state wins for "now".** Hand-edited data can break the replay. The
README's "pretend a week has passed" SQL moves concept dates but not answer dates, so a
replay can disagree with the stored row. The chart's last point, the memory panel, Due
today, Average tide "now" and the stage columns therefore always use the stored concept.
The replayed history is used only for the past. Where the two disagree, the line jumps to
the stored value at "now"; no repair of existing data is needed. The README's helper is
changed to shift answer times (`attempts.graded_at`, and session times) together with
concept dates, so it keeps the two consistent and spreads past answers over earlier
days, which is what produces the "3 days of answers" the chart needs (*Done when* 6).

### Daily AI-call cap (E6)

- A per-user count of AI calls per day, in the profile's time zone, stored in a small
  `llm_usage` table (user, day, count). Every call through the tutor counts, from the
  website and from the CLI alike, including citation retries. Web searches are not AI calls.
- The limit is a setting, `EBB_DAILY_AI_CALLS`. *(My call)* Default 300: high enough for
  long study days, below Groq's free daily request limit for the primary model. Check that
  limit when planning and set the default under it.
- The check runs before each call. Over the limit, the call is refused without contacting
  the provider (`UserQuotaExceeded`), nothing is written, and the user sees "Daily limit
  reached — your progress is saved. It resets at midnight." (in the website) or the same line
  and exit 1 (in the CLI). A refused grade keeps the question waiting, like a failed grade.

### Safety

- **Fence what isn't ours.** Topics, subject names and concept names go into prompts fenced
  and neutralised, the way answers and notes already are (`_neutralise`), and so does
  model-written question text when it is fed back into a later prompt
  ([m3-followups.md](../m3-followups.md), *Safety*).
- **Recognise a provider's safety refusal.** A refusal is not retried and doesn't fall back
  to another provider. The user sees "The tutor won't help with that topic." (or "…with
  these notes." during onboarding), and nothing is saved for that step.
- *(My call)* **A refusal mid-session follows the existing failure paths**, with the refusal
  message instead of the exhausted notice, so the graph gains no new route:
  - writing questions or extending the subject: the session ends through `summarize`, as
    when every provider fails there today;
  - grading: the same question stays waiting, so the learner can answer differently or end
    the session;
  - a lesson: skipped with the notice, the session goes on;
  - a re-check: "Couldn't re-check right now — the grade stands.", as today.
  A concept whose questions are always refused will end each session that reaches it. That
  is accepted as rare, since the concepts come from the learner's own topic or notes.
- No content filter of our own (still a later decision).

### The CLI

All commands keep working, against the same database, at the same time as the website.
`docker compose run --rm ebb …` still works (the `cli` profile). The CLI starts and resumes
sessions through the same shared code as the API, so the two can't drift. `ebb study` and
the website refuse to drive a session the other one is driving, with "That session is open
in another tab or terminal."

## Technical design

### Shape

```
browser ──HTTP + SSE──▶ Next.js (localhost:3000) ──▶ FastAPI (api) ──▶ Postgres (db)
                                                         │
                                                         └─ LangGraph quiz graph, Tutor (Groq), Tavily
```

- **Compose** gains two services, `api` (FastAPI, from the backend image) and `web`
  (Next.js production build). `docker compose up` starts `db`, `api` and `web`; the `ebb`
  CLI service stays behind the `cli` profile. Only port 3000 needs to be reachable from the
  browser. Postgres stays on 5432 for the dev loop.
- **API**: FastAPI in the existing `ebb` Python package (e.g. `ebb/api/`), sharing the
  store, the tutor and the graph. The graph and store are synchronous; the API runs them off
  the event loop (sync endpoints or a thread pool), and upload parsing runs off the event loop
  too (NFR-10). Migrations run at API start, as they do at CLI start.
- **Frontend**: a new top-level `frontend/` folder. Next.js (App Router) with TypeScript,
  CSS Modules ported from the prototype's variables and styles, Fraunces and Inter.
  *(My call)* The tide chart is the prototype's own SVG, ported, with no chart library
  (the PDF's Recharts goes with shadcn).
- **Versions**: the latest stable Next.js, React, Node LTS, FastAPI and uvicorn, checked
  online when planning (owner's standing preference).

### A shared session service

Today `cli.study` holds the start/resume logic: housekeeping (stale sessions, checkpoint
pruning), finding a resumable session, taking its lock, reading the pending payload or
finishing a cut-off step, the greeting, and invoking the graph. This moves into one module
that both the CLI and the API call. It also takes in the m2 follow-up "the resume branching
in `cli.study` could become one 'first graph input' decision".

### Who may drive a session

The CLI holds a session's advisory lock on a dedicated connection for as long as it runs.
A web session spans many HTTP requests, so that doesn't carry over unchanged. Requirement:
**one session is driven from one place at a time**. A second tab or a terminal gets "That
session is open in another tab or terminal." Two replies to the same question never both
apply (E3); replies already carry `attempt_id` and dispute ids, which make retries
idempotent. *(My call, the mechanism is the builder's)* For example, a lease per session held
by one browser tab and renewed while it is open, with each turn run under the
`thread_id` lock.

### API surface (sketch; exact routes are the builder's)

- Subjects: list (name, concept count, due count); create from a topic.
- Notes drafts: create a draft; add a file (multipart upload) with SSE progress; remove a
  file; commit the draft as a new subject with a name.
- Sources: list a subject's documents; add a file to a subject (SSE progress).
- Concepts: a subject's concepts with tide, stage, band, next review and the 3-day
  projection (the memory panel).
- Study: open a subject's session (returns the resume offer or starts fresh); resume or
  start fresh; send a reply (`{attempt_id, answer}`, `{dispute: {id, reason,
  explanation}}`, or `{quit: true}`) and receive SSE step events then the next payload or
  the summary.
- Dashboard: streak, due today, average tide and change, per-concept 30-day history, stages.
- Health: for the frontend to show the database/tutor errors above.

Every request body is validated at the boundary with Pydantic, so a malformed reply or
dispute id gets a 422, never a `KeyError` deep in the graph (m1, m3 follow-ups).

### Graph clean-ups, first (the deferred `/simplify` items)

Done before the API is built on the graph, each with tests unchanged in meaning:
1. One place that fills state defaults, at the resume boundary, instead of
   `state.get(key, default)` in many nodes.
2. One retry mechanism for citation fixes (question batches, grades, lessons, re-checks),
   ideally with the tutor returning only a citation fix.
3. A typed regrade result instead of the `(Grade, bool)` tuple.
4. One candidate-run object instead of the notes/web `generation_mode` flag.
5. Summary tallies from the store instead of correcting `tally` and `results` after a
   dispute.
6. Structured citation labels (domain, title, heading, page) instead of parsing the label
   string, which the frontend also needs for its popover.
7. One `current_question_id` instead of `pending_question_id` plus `retry_question`, and the
   lesson as `{text, citations}`.

Sessions saved by milestones 2 and 3 must still resume after these changes, as milestone 3
required of milestone 2's.

### Correctness fixes

- **A question left open for over 24 hours** (a tab left overnight) must not have its
  session closed by housekeeping while someone is still on it: touch `last_active_at` when
  a question is shown, or skip sessions whose lock is held (m2).
- **Answer counts skip voided answers** everywhere they read `attempts` (m3).
- **"Latest answer" means latest by `graded_at`**, not insertion order, so two sessions on
  one concept can't void the wrong snapshot (m3).
- **Advisory locks in a long-running server**: release a lock connection's locks on error
  inside the lock helper instead of routing every pool return through the reset callback
  (m2).
- **The due rule lives in one place**: `mastery.is_due` and the SQL in `count_due` must not
  drift; derive the counts from one definition (m2).
- **Subject names are unique per user**, ignoring case, in the database, so two
  simultaneous creates can't make two (m3). The migration must keep the owner's data; if
  their database already holds a duplicate name, the migration renames the newer one rather
  than failing.

### Schema: `004_…sql`

- `llm_usage (user_id, day date, calls int, primary key (user_id, day))`, with `user_id`
  referencing `profiles`.
- A unique index on `subjects (user_id, lower(name))`.
- Anything else the clean-ups or the lease need, with `user_id` on every new table (E9).

## Errors

Milestone 1–3 classes and messages are unchanged. New cases:

| Case | Handling | User sees |
|---|---|---|
| Daily cap reached | refuse before the call; nothing written | "Daily limit reached — your progress is saved. It resets at midnight." |
| Provider safety refusal | no retry, no fallback; nothing saved for that step | "The tutor won't help with that topic." |
| Malformed request | 422 at the boundary | a generic error card; never a traceback |
| Session driven elsewhere | refuse | "That session is open in another tab or terminal." |
| Upload refused | that file's row only | the CLI's message for that refusal |
| Concept call fails during onboarding | that row: **Try again** | the exhausted notice |
| Draft expired or API restarted | the draft is gone | "This upload expired — add the files again." |

## Testing

| Layer | What |
|---|---|
| Pure | the history replay ends at the stored concept state, voided answers skipped and changed ones regraded; streak (time zones, today/yesterday, best run, voided answers); average tide and its 30-day change; the 3-day projection; tide bands |
| Graph | everything in the existing graph tests, unchanged in meaning, after the clean-ups; milestone 2 and 3 checkpoints still resume |
| Session service | start, resume, resume of a cut-off step, start fresh, refusal when driven elsewhere; the CLI and the API both use it |
| API (FakeTutor, FakeWebSearch, Postgres) | each route's success and refusal paths; SSE step events in order, then the payload; a reply sent twice counts once; a dispute sent twice applies once; malformed bodies give 422; the draft is all-or-nothing (a failed concept call saves nothing; commit saves everything in one transaction); 5-file and duplicate refusals; cross-user isolation on every route (E1) |
| Cap and safety | the cap refuses before the provider is called and counts CLI and API calls together; a refusal isn't retried or fallen back from; fenced topic and concept names can't close a fence |
| Database | `004` applies to a milestone 3 database with data and keeps it (including a duplicate subject name); the unique name index; `llm_usage` counting by day in the profile's time zone |
| Frontend | components render every payload shape (MCQ, short, notes/web/general citations, general-knowledge label, lesson, notices, dispute states, summary, empty and error states); keyboard answering 1–4 and Enter; one browser test of a full session against the API with the fake tutor |

No test calls a real model or Tavily. CI runs the backend as today plus the frontend's
type check, lint and tests, and builds both images.

## Rollout

1. **Back up the owner's database before the first run on `main`**, because the API applies
   `003` and `004` on start: `docker exec ebb-db-1 psql -U ebb -d postgres -c "create
   database ebb_pre003 template ebb"` (no other connections to `ebb` open).
2. The README's "Run it" becomes `docker compose up` then open `localhost:3000`, keeps the
   CLI commands, documents `EBB_TIMEZONE` and `EBB_DAILY_AI_CALLS`, and its "Planned stack"
   drops shadcn and Tailwind for CSS Modules.

## Done when

1. All tests pass locally and in CI, and ruff and the frontend lint are clean.
2. On the owner's PC: `docker compose up`, then `localhost:3000` with an empty database
   leads to onboarding; a subject from a topic and a subject from a real lecture PDF can
   both be created, the PDF's row shows its concepts before **Start studying**, and a
   scanned PDF and a 21 MB file are refused on their rows.
3. A full session in the browser on the notes subject: a question "From your notes", a
   grade card whose chip quotes a sentence really on the cited page, a lesson after a miss,
   a dispute with a re-check result, **End session** and the summary. Each wait shows a
   step label within 2 seconds.
4. Closing the tab mid-question and opening the subject again offers **Resume**, which
   continues at the same question.
5. `ebb study` in a terminal works at the same time on the same subject, and refuses the
   session the browser is driving.
6. The dashboard shows the streak, due today, average tide and stages, and the chart once
   there are 3 days of answers (the README's updated "pretend a week has passed" SQL,
   which now shifts answer times too, produces them).
7. Setting `EBB_DAILY_AI_CALLS=1` shows the daily-limit message on the second call.
8. The owner's existing data is intact after `003` and `004`.

## Calls I made (check these)

- **No avatar or initials** in the header until sign-in.
- **The onboarding draft lives in the API's memory for an hour**; a restart discards it.
- **A ready file can be removed from the draft** before Start studying.
- **The subject name starts empty**, not guessed from a file name.
- **For notes, the name comes first** and is fixed once a file is being read (the concept
  call needs it); in topic mode the topic is the name.
- **A refresh or Study → Progress → Study reconnects silently** with the tab's own thread
  kept in browser session storage; any other return shows the Resume card and starts the
  thread from the current turn.
- **The dashboard's "now" always uses the stored concept**; the replay is only for the past,
  and the README's time-shift helper moves answer times too.
- **A safety refusal mid-session reuses the existing failure paths** (no new graph route).
- **Answers appear in the thread** as the learner's bubbles.
- **Flashcards, Exam, Suggested reading and per-concept Review buttons are hidden**, not
  greyed out.
- **No "Quiz ahead anyway"** in the nothing-due state (quizzing ahead is still a non-goal).
- **No "about 9 minutes"** estimate on the dashboard.
- **No "Stop generating"** button.
- **Switching subjects leaves the open session resumable.**
- **The tide chart is the prototype's SVG**, no chart library.
- **The daily cap defaults to 300 calls** and counts CLI and website calls together,
  including citation retries; web searches don't count.
- **One shared session service** for the CLI and the API.
- **A lease per session for the browser**, with the exact mechanism left to the builder.
- **Subject names become unique in the database**; an existing duplicate is renamed by the
  migration.
- **Mistral code stays** as an optional fallback; nothing new depends on it.
- **The theme choice is remembered in the browser.**

## Left to the builder

- Exact routes, payload shapes and SSE event names, within the sketch above.
- The session-lease mechanism, within the requirement.
- How LangGraph's stream is turned into step events (e.g. its `updates` or `debug` stream
  modes), within the 2-second rule.
- Sync vs async inside FastAPI, as long as the graph and parsing stay off the event loop.
- Whether the browser calls FastAPI directly or through a Next.js proxy route.
- The frontend's package manager and test tools.
- The exact SQL for `004`, as long as it is transactional and keeps existing data.
- How a safety refusal is detected for the Groq model in use.
- Component structure, as long as the screens match the prototype minus what this document
  removes.
