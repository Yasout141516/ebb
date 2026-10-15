# Ebb

An adaptive AI study tutor that quizzes you, grades your answers against your own
notes, teaches what you got wrong, and brings each concept back just before you
forget it.

Ebb is built around the Ebbinghaus forgetting curve: knowledge ebbs over time, and
well-timed review raises the tide again. It is a portfolio project in agent
engineering with LangGraph, where code makes every decision (scheduling, mastery,
routing) and the LLM only generates questions, grades and teaches.

## Status

Planning. The requirements, system design, prototype and plan reviews are done;
implementation starts with the core quiz loop in the terminal.

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
