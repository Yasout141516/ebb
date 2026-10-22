# Ebb — handoff for the next chat

Last updated 2026-10-22. Read this first, then the files it points to.

## What Ebb is

An adaptive AI study tutor (portfolio project, agent engineering with LangGraph). It quizzes
you, grades answers against your own notes with cited quotes, teaches what you got wrong, and
brings concepts back before you forget them (Leitner stages + a "tide" forgetting-curve gauge).
Code makes every decision; the LLM only generates questions, grades short answers and teaches.

- Repo: https://github.com/Yasout141516/ebb (public), local `E:\ebb`, branch `main`.
- Owner's plan: `Ebb — Requirements & System Design.pdf` (6 milestones), plan reviews in
  `docs/plan-reviews.md`, clickable UI prototype `ebb-prototype.html`.

## Milestones

| Milestone | Status |
|---|---|
| 1. Core quiz loop in the terminal (FR-1–FR-5) | Done, merged |
| 2. Spaced review + resumable sessions (FR-6, FR-7) | Done, merged at `05c0f1f` |
| 3. Uploads with cited answers, web questions, grade disputes, grading eval (FR-8, FR-9, NFR-2) | Done, merged to `main` and pushed on 2026-10-22 |
| 4. FastAPI backend + Next.js frontend from the prototype | Next; brainstorm paused (see below) |
| 5. Flashcards, exam mode, PDF export, resource search (FR-10–FR-13) | Later |
| 6. Auth (Supabase) and deployment (FR-14, NFR-13, NFR-14) | Later |

## Where things stand

- **Milestone 3 is merged to `main` and pushed (2026-10-22).** 17 tasks (14 planned + Task 16
  providers switch + Task 17 misconception cap), each reviewed, then a whole-branch review and
  its fix wave, `/code-review` (8 fixes) and `/simplify` (11 refactors). 575 tests pass, ruff is
  clean. The branch and its worktree were removed after the merge.
- Every decision taken on the owner's behalf: `docs/superpowers/m3-rulings.md`.
- **The owner will test milestone 3 hands-on once the milestone 4 frontend exists.** Until
  then nothing needs running.
- **The real `ebb` database is still at migration `002`.** Ebb applies pending migrations
  automatically the first time any `ebb` command (or the API) connects, so the first run on
  `main` will upgrade it to `003`. Back it up just before that first run:
  `docker exec ebb-db-1 psql -U ebb -d postgres -c "create database ebb_pre003 template ebb"`
  (no other connections may be open to `ebb`).
- **Owner's decision 2026-10-22: Groq only for now.** Mistral is abandoned until its account has
  an active plan; the owner was advised to comment out `MISTRAL_API_KEY` in `.env`.
- The real-data test used **`ebb_m3`**, a copy of the owner's database (created from template
  `ebb`) that is already at `003` and holds a "Software Architecture" subject made from the
  owner's two lecture PDFs (in `E:\ebb\notes\`). It can be dropped once the owner is happy
  (`drop database ebb_m3`) — ask first.
- Mistral is blocked (account has no active plan: 429 code 1300), so Groq has no working
  fallback today and `ebb eval build` can't run. Details in `docs/superpowers/m3-followups.md`.

Key documents, newest first:
- Milestone 3 design: `docs/superpowers/specs/2026-10-18-uploads-citations-disputes-design.md`
- Milestone 3 plan: `docs/superpowers/plans/2026-10-18-uploads-citations-disputes.md`
- Milestone 3 deferred items: `docs/superpowers/m3-followups.md`
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
- `llm/`: `tutor.py` (Groq primary, Mistral fallback, error classification), `schemas.py`,
  prompts in `llm/prompts/*.md`.
- `store.py` (Store protocol, every method takes `user_id` first), `db/` (psycopg 3 PgStore,
  SQL migrations `001`/`002`/`003`, advisory locks, full-text search over `document_chunks`).
- `cli.py` (Typer: `new [--notes FILE]`, `add`, `study`, `status`, `eval build|run`), `ui.py` (Rich).

Tests in `backend/tests/` (fakes: `MemoryStore`, `FakeTutor`, `FakeWebSearch`; test PDFs built
with reportlab). No test calls a real model or Tavily. CI: `.github/workflows/ci.yml`.

The grading eval runs with `ebb eval build` (a writer model generates 50 labelled cases into
`backend/evals/grading.jsonl`) and `ebb eval run` (the primary model grades them; a dated
report goes to `backend/evals/results/`). LangSmith is not used. The committed set was written
by Groq `qwen/qwen3.8-27b` (Mistral, the intended writer, is blocked) and graded by Groq
`openai/gpt-oss-120b`: baseline 70% (35 of 50), target 90%.

## Running it (Windows, Docker Desktop must be running)

```powershell
cd E:\ebb
docker compose up -d                       # Postgres 18.6 (container ebb-db-1)
docker compose run --rm ebb study          # or: new "<topic>" [--notes notes/x.pdf], add, status
cd backend; $env:EBB_REQUIRE_DB=1; $env:LANGGRAPH_STRICT_MSGPACK='true'; uv run pytest -q
```

- `.env` lives at `E:\ebb\.env` (git-ignored). Never print or commit it.
  - `GROQ_API_KEY` (required).
  - `MISTRAL_API_KEY` (optional fallback, and the `eval build` writer; leave it out while the
    Mistral account is inactive, or every Groq failure also waits on a Mistral 429).
  - `TAVILY_API_KEY` (optional: web questions when Ebb repeats itself).
  - Optional: `EBB_TIMEZONE` (e.g. `Asia/Dhaka`; Docker defaults to UTC), `EBB_PRIMARY_MODEL`,
    `EBB_FALLBACK_MODEL`.
- Default models: `openai/gpt-oss-120b` on Groq (primary), `mistral-medium-latest` (fallback).
  Providers retire models; if one disappears, Ebb exits with
  `Model '…' was not found — set EBB_PRIMARY_MODEL…`. Check current models before changing.
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
- Also runs `/code-review` and `/simplify` before finishing a milestone; both were done for M1,
  M2 and M3 (run `/simplify` after review fixes, not in parallel with a reviewer).
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

## Next steps (milestone 4)

1. **Start here:** resume the milestone 4 brainstorm (`superpowers:brainstorming`), paused on
   2026-10-20 while milestone 3 finished. The owner's decisions so far — don't ask again:
   - Runs only on the owner's PC: `docker compose up` starts the database, the API and the
     website on localhost:3000. No online deploy until sign-in (milestone 6).
   - Screens: onboarding (adding a subject or notes matters most), the study workspace (Quiz)
     and the progress dashboard. **Chat mode is dropped.** Flashcards/Exam stay in milestone 5,
     sign-in in milestone 6.
   - Styling: port the prototype's CSS (variables + styles) into CSS Modules — no
     Tailwind/shadcn.
   - Citation popover: **no** "Open page N in notes" link (uploaded files aren't stored; only
     their extracted text is).
   - Still to cover: play back the onboarding guesses (single dev user, the CLI keeps working,
     Flashcards/Exam buttons hidden — not yet confirmed), then the study workspace, the
     dashboard, streaming (NFR-1), and the "Before the milestone 4 API" items in
     `docs/superpowers/m2-followups.md` and `docs/superpowers/m3-followups.md` (the graph
     refactors deferred from `/simplify` are listed there).
   Then the usual flow: written design → `writing-plans` → subagent-driven development in a
   new worktree → `/code-review` → `/simplify` → merge with the owner's OK.
2. Before the first `ebb` run on `main` (including milestone 4's API against the real
   database): back up `ebb` (command above); the run then migrates it to `003`.
3. When Mistral's account is active: re-test it, then rebuild the eval set with Mistral as the
   writer (`cd backend; uv run ebb eval build --force`, then `eval run`) and commit the results.
4. Open decisions carried from the plan: the backend host for milestone 6 (Cloud Run vs Render,
   see `docs/plan-reviews.md`).
5. `docs/superpowers/m3-followups.md` also lists the safety gaps (before other people use Ebb),
   eval improvements (stricter grading prompt, stricter writer) and terminal polish.
