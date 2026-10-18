# Ebb — handoff for the next chat

Last updated 2026-10-18. Read this first, then the files it points to.

## What Ebb is

An adaptive AI study tutor (portfolio project, agent engineering with LangGraph). It quizzes
you, grades answers, teaches what you got wrong, and brings concepts back before you forget
them (Leitner stages + a "tide" forgetting-curve gauge). Code makes every decision; the LLM
only generates questions, grades short answers and teaches.

- Repo: https://github.com/Yasout141516/ebb (public), local `E:\ebb`, branch `main`.
- Owner's plan: `Ebb — Requirements & System Design.pdf` (6 milestones), plan reviews in
  `docs/plan-reviews.md`, clickable UI prototype `ebb-prototype.html`.

## Where things stand

| Milestone | Status |
|---|---|
| 1. Core quiz loop in the terminal (FR-1–FR-5) | Done, merged |
| 2. Spaced review + resumable sessions (FR-6, FR-7) | Done, merged at `05c0f1f`, 296 tests passing, owner tested it live |
| 3. Uploads with cited answers, grade disputes, grading eval (FR-8, FR-9, NFR-2) | **Next** |
| 4. FastAPI backend + Next.js frontend from the prototype | Later |
| 5. Flashcards, exam mode, PDF export, resource search (FR-10–FR-13) | Later |
| 6. Auth (Supabase) and deployment (FR-14, NFR-13, NFR-14) | Later |

Key documents, newest first:
- Milestone 2 design: `docs/superpowers/specs/2026-10-16-spaced-review-design.md`
- Milestone 2 plan: `docs/superpowers/plans/2026-10-16-spaced-review.md`
- Milestone 1 design and plan: `docs/superpowers/specs/2026-10-15-core-loop-design.md`,
  `docs/superpowers/plans/2026-10-15-core-loop.md`
- Deferred items: `docs/superpowers/m1-followups.md`, `docs/superpowers/m2-followups.md`

## How the code is laid out

`backend/src/ebb/`: `mastery.py` (Leitner + tide, pure), `routing.py` (concept/question choice,
graph edges, pure), `grading.py` (MCQ in code), `graph/` (ten-node LangGraph quiz graph, JSON-only
state), `llm/` (Gemini primary, Groq backup, prompts in `llm/prompts/*.md`), `store.py` (Store
protocol, every method takes `user_id` first), `db/` (psycopg 3 PgStore, SQL migrations
`001`/`002`, advisory locks), `cli.py` (Typer: `new`, `study`, `status`), `ui.py` (Rich).
Tests in `backend/tests/` (fakes: `MemoryStore`, `FakeTutor`). CI: `.github/workflows/ci.yml`.

## Running it (Windows, Docker Desktop must be running)

```powershell
cd E:\ebb
docker compose up -d                       # Postgres 18.6 (container ebb-db-1)
docker compose run --rm ebb study          # or: new "<topic>", status
cd backend; $env:EBB_REQUIRE_DB=1; $env:LANGGRAPH_STRICT_MSGPACK='true'; uv run pytest -q
```

- `.env` lives at `E:\ebb\.env` (git-ignored): `GOOGLE_API_KEY`, `GROQ_API_KEY`, optional
  `EBB_TIMEZONE` (e.g. `Asia/Dhaka`; Docker defaults to UTC). Never print or commit it.
- Default models: `gemini-3.8-flash` (primary), `qwen/qwen3.8-27b` on Groq (backup). Both earlier
  defaults were retired by the providers; if a model disappears, Ebb exits with
  `Model '…' was not found — set EBB_PRIMARY_MODEL…`. Check current models before changing.
- Tests use database `ebb_test` in the same container; the owner's real data is database `ebb`.
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
- Also runs `/code-review` and `/simplify` before finishing a milestone; both were done for M1
  and M2 (run `/simplify` after review fixes, not in parallel with a reviewer).
- Wants the **latest stable versions** of tools and libraries (Python 3.14, Postgres 18.6,
  uv 0.13, LangGraph 1.2, …). Check versions online before pinning; some GitHub Actions only
  publish full tags (e.g. `astral-sh/setup-uv@v10.3.0`, not `@v10`).
- Push to GitHub only with the owner's OK. The owner is learning Docker and the stack — explain
  concepts plainly when asked (they have asked about Docker, Compose, volumes, tide vs stage).
- At the end of each milestone, list every decision you made on their behalf ("Rulings I made").

## Next steps (milestone 3)

1. Start with `superpowers:brainstorming` for milestone 3: uploads (PDF/TXT/MD, 20 MB per file,
   5 files per subject, scanned-PDF rejection per design decision D2), Postgres full-text
   retrieval over `document_chunks`, cited answers with `verify_citations` (unknown chunk IDs
   rejected, at most two retries, "General knowledge" label otherwise), grade disputes (FR-9),
   and the grading eval (NFR-2: 30–50 hand-labelled answers in LangSmith, target ≥ 90% agreement).
   The owner must hand-label the eval set; plan for that.
2. Open decisions carried from the plan: chunking parameters for `document_chunks`; the backend
   host for milestone 6 (Cloud Run vs Render, see `docs/plan-reviews.md`).
3. Consider the follow-ups in `docs/superpowers/m2-followups.md` first section (subject growth by
   near-duplicates, resume numbering after a cut-off lesson) when they touch milestone 3 code.
