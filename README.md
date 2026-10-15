# Ebb

An adaptive AI study tutor that quizzes you, grades your answers against your own
notes, teaches what you got wrong, and brings each concept back just before you
forget it.

Ebb is built around the Ebbinghaus forgetting curve: knowledge ebbs over time, and
well-timed review raises the tide again. It is a portfolio project in agent
engineering with LangGraph, where code makes every decision (scheduling, mastery,
routing) and the LLM only generates questions, grades and teaches.

## Status

Milestone 1 of 6 is built: the core quiz loop in the terminal, with learning
state in Postgres. Next: spaced review across sessions and resumable sessions.

## Run it

You need Docker and a free Gemini API key from https://aistudio.google.com.

```bash
cp .env.example .env              # then put your key in GOOGLE_API_KEY
docker compose up -d              # starts Postgres
docker compose run --rm ebb new "Computer Networks"
docker compose run --rm ebb study
docker compose run --rm ebb status
```

For development, with [uv](https://docs.astral.sh/uv/):

```bash
docker compose up -d
cd backend
uv sync
uv run pytest                     # 179 tests; database tests need the compose db
uv run ebb study
```

## How it works

`ebb study` runs a LangGraph graph: pick the concept most in need of review,
generate a batch of questions, ask one (the graph pauses with `interrupt`),
grade it (multiple choice in code, short answers by the LLM), update the
concept's Leitner stage, then route: teach on a miss, a harder question after a
correct answer, the next concept after two correct in a row. Every routing and
scheduling decision is plain Python with tests; the LLM only writes, grades and
teaches.

## What's here

| File | What it is |
|---|---|
| [Ebb — Requirements & System Design.pdf](Ebb%20—%20Requirements%20&%20System%20Design.pdf) | Requirements, screens, agent graph, database schema and build milestones |
| [ebb-prototype.html](ebb-prototype.html) | Clickable prototype of every screen and system state, with mock data (open in a browser) |
| [docs/plan-reviews.md](docs/plan-reviews.md) | Design and engineering reviews of the plan, with the amendments they made |

## Planned stack

LangGraph + LangChain · Gemini (Groq fallback) · FastAPI · Supabase Postgres, Auth
and Storage · Postgres full-text search · Next.js + shadcn/ui · LangSmith for
tracing and the grading eval. Free tiers throughout.
