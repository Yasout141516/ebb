# Ebb — handoff for the next chat

Last updated 2026-10-31. Read this first, then the files it points to.

## What Ebb is

An adaptive AI study tutor (portfolio project, agent engineering with LangGraph). It quizzes
you, grades answers against your own notes with cited quotes, teaches what you got wrong, and
brings concepts back before you forget them (Leitner stages + a "tide" forgetting-curve gauge).
Code makes every decision; the LLM only generates questions, grades short answers and teaches.

- Repo: https://github.com/Yasout141516/ebb (public), local `E:\ebb`, branch `main` (milestone 4
  merged at `7cd4589` and pushed on 2026-10-27; its branch and worktree are removed).
- Owner's plan: `Ebb — Requirements & System Design.pdf` (6 milestones), plan reviews in
  `docs/plan-reviews.md`, clickable UI prototype `ebb-prototype.html`.

## Milestones

| Milestone | Status |
|---|---|
| 1. Core quiz loop in the terminal (FR-1–FR-5) | Done, merged |
| 2. Spaced review + resumable sessions (FR-6, FR-7) | Done, merged at `05c0f1f` |
| 3. Uploads with cited answers, web questions, grade disputes, grading eval (FR-8, FR-9, NFR-2) | Done, merged to `main` and pushed on 2026-10-22 |
| 4. FastAPI backend + Next.js frontend from the prototype | Done, merged to `main` and pushed on 2026-10-27 (`7cd4589`); the owner's hands-on test is still to do |
| Pre-5: shuffled options, LLM gateway + OpenRouter, Costs page, learn phase | Built on branch `pre-m5-learn-gateway-costs` (not merged yet; the owner tests it hands-on) |
| 5. Flashcards, exam mode, PDF export, resource search (FR-10–FR-13) | **Next** — not started |
| 6. Auth (Supabase) and deployment (FR-14, NFR-13, NFR-14) | Later |

## Where things stand

- **The pre-milestone-5 round is built** on branch `pre-m5-learn-gateway-costs` (worktree
  `.claude/worktrees/pre-m5`); its whole-branch review is done and
  its fix wave applied; a live smoke run with real keys is still needed before merge. Spec:
  `docs/superpowers/specs/2026-10-27-learn-phase-gateway-costs-design.md`; the 33 decisions taken
  on the owner's behalf and the deferred minor items: `docs/superpowers/pre-m5-rulings.md`.
  What it added:
  - multiple-choice options are shuffled in code before a question is saved;
  - an LLM gateway (`llm/gateway.py`) with three roles (teach, quiz, eval writer) chosen from
    `.env` (`EBB_TEACH_MODEL`, `EBB_QUIZ_MODEL`, `EBB_EVAL_WRITER_MODEL`, backups
    `EBB_TEACH_BACKUP`/`EBB_QUIZ_BACKUP`; `EBB_PRIMARY_MODEL`/`EBB_FALLBACK_MODEL` remain the
    defaults), OpenRouter as a third provider (`OPENROUTER_API_KEY`), every call streamed and
    recorded per attempt in `llm_calls` (tokens, first-token and total time, estimated cost,
    whether the backup served it);
  - the dev-only Costs page (`/costs`, `EBB_SHOW_COSTS`, default on; to be hidden in milestone
    6): costs are estimates for a paid plan; only Groq `openai/gpt-oss-120b` has a price
    (`llm/prices.py`, from Groq's page); Mistral and OpenRouter models show "price unknown";
  - the learn phase on the web (`learn/` package, learn routes, migrations `005` and `006`):
    a lesson in the `/teach-me` format from notes plus Tavily, streamed; check-yourself
    questions answered on request; suggested follow-ups; "I'm ready, quiz me" hands over to the
    quiz, which starts with multiple choice; "Read the lesson again" for learned concepts. Q&A
    there never changes mastery. The CLI is unchanged (it still opens a concept with a question).
  - The browser test now covers lesson, "Answer this for me", a suggestion chip, ready, a
    multiple-choice answer and the Costs page. `backend/tests/e2e_server.py` records
    representative `LlmCall`s for its fake tutor's calls, since the fake bypasses the gateway.
  - **Before the first start of this branch against the real database**, back it up as
    `ebb_pre005` (README, Run it): the API migrates `ebb` to `006`. The branch adds Python
    (`langchain-openai`) and npm (`react-markdown`, `remark-gfm`) packages, so start it with
    `docker compose up --build` (or `docker compose build` first): plain `up` reuses old images.

- **Milestone 4 is merged and pushed (2026-10-27).** `docker compose up` starts the database,
  the API (port 8000) and the website (http://localhost:3000), all bound to this PC only:
  onboarding (a typed topic or uploaded notes), the study workspace (questions, grades,
  lessons, citations, disputes, the memory panel, End session) and the progress dashboard,
  built like `ebb-prototype.html`. Chat is dropped; Flashcards/Exam are hidden until
  milestone 5. Done: 26 tasks, each reviewed; a whole-branch review and its fix wave;
  `/code-review` (7 fixes, 3 deferred) and `/simplify` (11 refactors). Checks at the merge: 837 backend
  tests, 206 frontend tests (1090 and 260 after the pre-5 round), ruff, typecheck, lint and one browser test (`npm run e2e`).
  The 46 decisions taken on the owner's behalf: `docs/superpowers/m4-rulings.md`; minor
  leftovers: `docs/superpowers/m4-followups.md`.
- Milestone 3 is merged to `main` (2026-10-22). Every decision taken on the owner's behalf:
  `docs/superpowers/m3-rulings.md`.
- **The owner tests milestone 4 hands-on** (the spec's *Done when* list is the acceptance check).
- **The real `ebb` database is still at migration `002`.** The API (and any `ebb` command)
  applies pending migrations when it first connects, so the first `docker compose up` on this
  code upgrades it to `004`, and the pre-5 branch then to `006`. Back it up first, with nothing
  else connected to `ebb` (`docker compose up -d db`, then):
  `docker exec ebb-db-1 psql -U ebb -d postgres -c "create database ebb_pre005 template ebb"`
- Set `EBB_TIMEZONE` in `.env` (e.g. `Asia/Dhaka`): Docker defaults to UTC, while the website
  shows dates in the browser's zone (R35). `EBB_DAILY_AI_CALLS` (default 300) caps AI calls per
  user per day.
- **Owner's decision 2026-10-22: Groq only for now.** Mistral is abandoned until its account has
  an active plan; the owner was advised to comment out `MISTRAL_API_KEY` in `.env`.
- The real-data test used **`ebb_m3`**, a copy of the owner's database (created from template
  `ebb`) that is already at `003` and holds a "Software Architecture" subject made from the
  owner's two lecture PDFs (in `E:\ebb\notes\`). It can be dropped once the owner is happy
  (`drop database ebb_m3`) — ask first.
- Mistral is blocked (account has no active plan: 429 code 1300), so Groq has no working
  fallback today and `ebb eval build` can't run. Details in `docs/superpowers/m3-followups.md`.

Key documents, newest first:
- Pre-5 design and rulings: `docs/superpowers/specs/2026-10-27-learn-phase-gateway-costs-design.md`,
  `docs/superpowers/pre-m5-rulings.md`
- Milestone 4 rulings: `docs/superpowers/m4-rulings.md`
- Milestone 4 design: `docs/superpowers/specs/2026-10-23-web-app-design.md`
- Milestone 4 plan: `docs/superpowers/plans/2026-10-23-web-app.md`
- Milestone 4 deferred items: `docs/superpowers/m4-followups.md`
- Milestone 3 design: `docs/superpowers/specs/2026-10-18-uploads-citations-disputes-design.md`
- Milestone 3 plan: `docs/superpowers/plans/2026-10-18-uploads-citations-disputes.md`
- Milestone 3 deferred items: `docs/superpowers/m3-followups.md` (milestone 4 resolved many)
- Milestone 2 design and plan: `docs/superpowers/specs/2026-10-16-spaced-review-design.md`,
  `docs/superpowers/plans/2026-10-16-spaced-review.md`
- Milestone 1 design and plan: `docs/superpowers/specs/2026-10-15-core-loop-design.md`,
  `docs/superpowers/plans/2026-10-15-core-loop.md`
- Earlier deferred items: `docs/superpowers/m1-followups.md`, `docs/superpowers/m2-followups.md`

## How the code is laid out

`backend/src/ebb/`:
- `mastery.py` (Leitner + tide, pure), `routing.py` (concept/question choice, graph edges,
  pure), `grading.py` (MCQ in code, dispute outcomes).
- `graph/`: the fourteen-node LangGraph quiz graph, JSON-only state: `load_learner`,
  `pick_concept`, `extend_subject`, `retrieve_notes`, `generate_questions`, `verify_citations`,
  `search_web`, `ask_question`, `grade_answer`, `regrade`, `update_mastery`, `teach`,
  `next_concept`, `summarize`.
- `notes.py` (upload checks, PDF/TXT/MD text extraction), `chunking.py` (passages with page
  offsets), `citations.py` (labels, quote verification, pages), `repeats.py` (repeated-question
  detection), `web.py` (Tavily search), `evals.py` (the grading eval).
- `llm/`: `tutor.py` (the Tutor: one method per kind of work, each routed to a role),
  `gateway.py` (streams every call, retries, backup, records `LlmCall`s), `providers.py`
  (Groq/OpenRouter/Mistral), `prices.py`, `schemas.py`, prompts in `llm/prompts/*.md`.
- `learn/` (the learn phase: its LangGraph graph and the `LearnSessions` service), `costs.py`
  (pure sums and anomaly rules for the Costs page), `api/routes_learn.py`, `api/routes_costs.py`.
- `store.py` (Store protocol, every method takes `user_id` first), `db/` (psycopg 3 PgStore,
  SQL migrations `001`-`006`, advisory locks, full-text search over `document_chunks`).
- `cli.py` (Typer: `new [--notes FILE]`, `add`, `study`, `status`, `serve`, `eval build|run`),
  `ui.py` (Rich).
- `study.py` (`StudySessions`: the one service the CLI and the API both drive the graph through:
  start, resume, reply, discard, heartbeat, release, driver leases) and `subjects.py` (creating
  subjects from a topic or notes, adding notes, shared checks and refusal copy).
- `api/` (FastAPI: `app.py` factory and `/health`, `routes_subjects.py`, `routes_notes.py` with
  drafts and SSE uploads, `routes_study.py` SSE sessions, `routes_progress.py`, `schemas.py`,
  `services.py`, `drafts.py`, `errors.py`); `progress.py` (streak, average tide, chart data).
- `frontend/` (Next.js 16, React 19, CSS Modules, no UI library): `app/` pages (`new`,
  `study/[id]`, `progress/[id]`, `subjects/[id]/add`, `costs`), `components/` (`onboarding`, `study`,
  `progress`, `costs`), `lib/` (API client, SSE reader, types, session reducer), `e2e/` (Playwright).
  All data is fetched in the browser from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

Tests in `backend/tests/` (fakes: `MemoryStore`, `FakeTutor`, `FakeWebSearch`; test PDFs built
with reportlab). No test calls a real model or Tavily. CI: `.github/workflows/ci.yml` (backend,
frontend, e2e and docker jobs). `backend/tests/e2e_server.py` is the API the browser test runs
against (memory store, fake tutor, no database).

The grading eval runs with `ebb eval build` (a writer model generates 50 labelled cases into
`backend/evals/grading.jsonl`) and `ebb eval run` (the primary model grades them; a dated
report goes to `backend/evals/results/`). LangSmith is not used. The committed set was written
by Groq `qwen/qwen3.8-27b` (Mistral, the intended writer, is blocked) and graded by Groq
`openai/gpt-oss-120b`: baseline 70% (35 of 50), target 90%.

## Running it (Windows, Docker Desktop must be running)

```powershell
cd E:\ebb
docker compose up                          # database, API (8000) and website: http://localhost:3000
docker compose up -d db                    # only Postgres 18.6 (container ebb-db-1), for tests
docker compose run --rm ebb study          # the terminal app: new "<topic>" [--notes x.pdf], add, status
cd backend; $env:EBB_REQUIRE_DB=1; $env:LANGGRAPH_STRICT_MSGPACK='true'; uv run pytest -q
cd frontend; npm test; npm run typecheck; npm run lint; npm run e2e   # e2e: npx playwright install chromium once
```

- `.env` lives at `E:\ebb\.env` (git-ignored). Never print or commit it.
  - `GROQ_API_KEY` (required).
  - `MISTRAL_API_KEY` (optional fallback, and the `eval build` writer; leave it out while the
    Mistral account is inactive, or every Groq failure also waits on a Mistral 429).
  - `TAVILY_API_KEY` (optional: web questions when Ebb repeats itself).
  - Optional: `EBB_TIMEZONE` (e.g. `Asia/Dhaka`; Docker defaults to UTC), `EBB_DAILY_AI_CALLS`
    (default 300), `EBB_WEB_ORIGIN` (comma-separated; default
    `http://localhost:3000,http://127.0.0.1:3000`), `EBB_PRIMARY_MODEL`, `EBB_FALLBACK_MODEL`
    (legacy), `EBB_TEACH_MODEL`, `EBB_QUIZ_MODEL`, `EBB_EVAL_WRITER_MODEL`, `EBB_TEACH_BACKUP`,
    `EBB_QUIZ_BACKUP`, `OPENROUTER_API_KEY`, `EBB_SHOW_COSTS`. All are in `.env.example`.
- Default models: `openai/gpt-oss-120b` on Groq for teach and quiz, `mistral-medium-latest` as
  backup and eval writer. Providers retire models; if one disappears, Ebb exits with a message
  naming the setting to change. Check current models before changing.
- Kaspersky Anti-Virus on the owner's PC intercepts TLS to `api.mistral.ai` (not to Groq);
  Kaspersky exclusion vs `truststore` is undecided.
- Databases in the same container: `ebb` (the owner's real data), `ebb_test` (pytest),
  `ebb_m3` (milestone 3 test copy, see above).
- Docker volumes: `ebb_ebb-pgdata` (live data, has the owner's "Computer Networks" subject),
  `ebb_ebb-pgdata-m1-backup` (backup taken before migration 002), `m1-core-loop_ebb-pgdata`
  (older copy). The two backups can be deleted once the owner is happy.
- If you work in a git worktree, Compose names things after the folder; use `docker compose -p ebb`
  to reach the real database, and never `down -v`.

## How the owner likes to work

- Uses the Superpowers workflow: brainstorming → written design → `writing-plans` →
  subagent-driven development (fresh implementer + reviewer per task, whole-branch review at
  the end) in an isolated git worktree, then `finishing-a-development-branch` (local merge to
  `main`, then push).
- In brainstorming, give **options to choose from** (via questions), not "my defaults".
- Also runs `/code-review` and `/simplify` before finishing a milestone; both were done for M1–M4
  (run `/simplify` after review fixes, not in parallel with a reviewer; have a reviewer check
  `/simplify`'s refactors — in M4 one of them dropped a lease renewal).
- **The UI must look like `ebb-prototype.html`** (owner, 2026-10-23): layout, spacing, colours,
  fonts, icons and copy, minus agreed removals. Check every screen with side-by-side screenshots
  (app vs prototype) at 1440×900 and 375 px.
- Docker is the only way to run Ebb (owner declined a native dev mode), so the local build
  matches what goes live.
- Subagents on smaller models follow their own attribution reminder: tell every implementer the
  exact commit trailer (`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
- Account limits (session/weekly) can stop subagents mid-task; their work is saved — resume the
  same agent after the reset rather than starting a new one.
- Free tiers only; two LLM providers at most (decided 2026-10-21). Gemini was removed for
  frequent 503 overloads and answer quality.
- When the owner steps away: pause cleanly (commit or WIP-commit, update the ledger), and shut
  Docker down if asked (`docker stop ebb-db-1`, `docker desktop stop`) — never delete volumes.
- Wants the **latest stable versions** of tools and libraries (Python 3.14, Postgres 18.6,
  uv 0.13, LangGraph 1.2, …). Check versions online before pinning; some GitHub Actions only
  publish full tags (e.g. `astral-sh/setup-uv@v10.3.0`, not `@v10`).
- Push to GitHub only with the owner's OK. The owner is learning Docker and the stack — explain
  concepts plainly when asked (they have asked about Docker, Compose, volumes, tide vs stage).
- At the end of each milestone, list every decision you made on their behalf ("Rulings I made").

## Next steps

0. **Pre-5 branch:** the whole-branch review and merge (with the owner's OK), after the owner
   tries it: back up `ebb_pre005`, `docker compose up --build`, open a new concept (lesson streams,
   "Answer this for me", a chip, "I'm ready, quiz me", first question is multiple choice), then
   look at Costs. Worth a live check: that Groq returns token usage while streaming (else the
   Costs rows honestly show "not reported"). Deferred: switching models when the main one is
   slow, and token-saving tweaks (`m4-followups.md`, "Found in use").
1. **The owner tests milestone 4 hands-on** against the spec's *Done when* list:
   - back up `ebb` first (above; restore command in the README), set `EBB_TIMEZONE`;
   - `docker compose up` (first build takes a few minutes), open http://localhost:3000;
   - create a subject from a topic and one from a lecture PDF in `notes/`, study a full session
     (incl. a dispute, a refresh mid-session, closing the tab and resuming), check the
     dashboard, and run `ebb study` in a terminal at the same time;
   - review the "Worth a look with the owner" items at the top of `m4-followups.md` (invented
     add-notes copy, the subject gauge metric, "due yesterday", R30 tide note, amber fall,
     straight apostrophes, "p. 12").
   Fix what the owner reports before starting milestone 5.
2. **Milestone 5: flashcards, exam mode, PDF export, resource search (FR-10–FR-13).** Start with
   `superpowers:brainstorming` (options to choose from). Inputs the brainstorm should cover:
   - the prototype's screens `04 Flashcards`, `05 Exam`, `05b Exam results` (build them like
     the prototype, as in M4) and where their entry points go back in (the workspace's hidden
     Flashcards/Exam chips, "Review weak cards", "Take a 5-question exam", the due list's
     Review buttons — all removed in M4);
   - plan-review decisions that bind M5: D1 (exam timer warnings at 5:00/1:00, auto-submit,
     unanswered = incorrect), D5 (flashcards with no weak concepts show "Tide's high…"; exam
     grading progress with per-question error cards), E2 (flashcard self-ratings follow
     "promote only when due"), E7 (exam answers saved as picked; the server is the clock;
     short answers graded 2 at a time);
   - FR-12 resource search: Tavily is already wired (`web.py`); YouTube was planned; the spec
     says only returned URLs can be cited. FR-13 PDF export: the plan names WeasyPrint +
     Supabase Storage, but Supabase is milestone 6 — decide where the PDF goes locally;
     the CEO review noted FR-12/FR-13 as candidates to cut if time runs short;
   - schema: the plan's `flashcards`, `exams`, `exam_questions` tables, attempts gaining
     `flashcard_id`/`exam_id` (check constraint: exactly one of question/flashcard), every new
     table with `user_id` (E9);
   - milestone 5 starts from the pre-5 branch merged: lessons are stored per concept, every
     call is recorded per user (`llm_calls`), and the gateway routes by role (new flashcard or
     exam work needs an `Endpoint` in `llm/gateway.py` and a row in `costs.ENDPOINTS`);
   - the free-tier budget: Groq's free tier for `openai/gpt-oss-120b` is about 1,000 requests
     and 200,000 tokens a day; the daily cap is 300 calls; Mistral is still inactive.
   Then the usual flow: written design → `writing-plans` → subagent-driven development in a new
   worktree → final review → `/code-review` → `/simplify` → merge with the owner's OK.
3. When Mistral's account is active: re-test it, then rebuild the eval set with Mistral as the
   writer (`cd backend; uv run ebb eval build --force`, then `eval run`) and commit the results.
4. Milestone 6 (auth + deploy): JWT verification moved here from M4 (E9 amendment); the backend
   host is still open (Cloud Run vs Render, see `docs/plan-reviews.md`).
5. Leftovers: `docs/superpowers/m4-followups.md` (incl. what `/simplify` deferred: one failure
   mapping, a per-instance locking strategy, server-side same-driver wait, turn ids in the
   payload…), `m3-followups.md` (eval improvements, terminal polish).
6. Housekeeping the owner can approve: drop `ebb_m3` and the two old Docker volume backups
   (`ebb_ebb-pgdata-m1-backup`, `m1-core-loop_ebb-pgdata`) once happy; an empty folder
   `.claude/worktrees/m4-web-app` may be left behind (safe to delete).
