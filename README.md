# Ebb

An adaptive AI study tutor that quizzes you, grades your answers against your own
notes, teaches what you got wrong, and brings each concept back just before you
forget it.

Ebb is built around the Ebbinghaus forgetting curve: knowledge ebbs over time, and
well-timed review raises the tide again. It is a portfolio project in agent
engineering with LangGraph, where code makes every decision (scheduling, mastery,
routing) and the LLM only generates questions, grades and teaches.

## Status

Milestone 4 of 6 is built: Ebb now has a website (onboarding, the study workspace and a
progress dashboard) on top of a FastAPI backend, next to the terminal app. Milestone 3 added
notes uploads with cited answers, web questions when Ebb repeats itself, grade disputes and a
grading eval (first baseline: 70%). Since then, a pre-milestone-5 round is done: shuffled
multiple-choice options, a role-based LLM gateway (with OpenRouter) that records tokens, time and
estimated cost for every call, a dev-only Costs page, and a learn phase before the quiz.
Next: flashcards and exam mode (milestone 5).

## Run it

You need Docker and a free Groq API key from https://console.groq.com.

**If you already have an Ebb database, back it up first.** The API upgrades the `ebb` database
to the newest migration the first time it connects, so before the first `docker compose up` run
this, with nothing else connected to `ebb` (`up -d db` starts only the database):

```bash
docker compose up -d db
docker exec ebb-db-1 psql -U ebb -d postgres -c "create database ebb_pre005 template ebb"
```

(Older copies, such as `ebb_pre003`, are not touched. This round's migrations are `005` and
`006`, so the copy must be taken before the first start of this version.)

To go back to that copy later, stop the app first (`docker compose stop api web`, and close any
`ebb` terminal), then:

```bash
docker exec ebb-db-1 psql -U ebb -d postgres -c "drop database ebb" -c "create database ebb template ebb_pre005"
```

Then:

```bash
cp .env.example .env              # then put your key in GROQ_API_KEY
docker compose up --build         # starts Postgres, the API (port 8000) and the website
```

Open http://localhost:3000. The first start builds the images, which takes a few minutes.
After pulling a new version (this one adds Python and npm packages), use `--build` again:
plain `docker compose up` reuses the images it already has. The
website, the API and the database are reachable from this computer only: Ebb has no sign-in yet.

The terminal app still works, next to the website and on the same data:

```bash
docker compose run --rm ebb new "Computer Networks"
docker compose run --rm ebb study
docker compose run --rm ebb status
```

Settings in `.env`:

- `EBB_TIMEZONE`: your IANA time zone, for example `Asia/Dhaka`. Docker defaults to UTC, which
  can name the wrong day near midnight. The website shows dates in the browser's time zone, so
  set this to match it: lines the server writes ("Next review: Thursday") and "due today" use it.
  Outside Docker, Ebb uses the computer's time zone, or UTC if it can't tell. A name Ebb doesn't
  know stops the terminal app; the website says so in place of its pages (the API uses UTC).
- `EBB_DAILY_AI_CALLS`: the most AI calls (questions, grades, lessons) one user may make per day,
  counted in that time zone. Default 300; the website and the terminal show a clear message when
  it is reached.
- `EBB_WEB_ORIGIN`: the website's address, which the API allows; several separated by commas
  (default `http://localhost:3000,http://127.0.0.1:3000`).

Ebb grades and writes with Groq (`openai/gpt-oss-120b`) unless you choose other models below.
Optional keys in `.env`:
`MISTRAL_API_KEY` (free plan at https://console.mistral.ai) adds Mistral as a fallback when
Groq fails, and is needed for `ebb eval build`. `TAVILY_API_KEY` (free at
https://app.tavily.com) lets Ebb replace a question it is about to repeat with one written
from a web search, and feeds the learn phase's lessons; without it, repeated questions are just
dropped and lessons use your notes only.

### Models, OpenRouter and what calls cost

Every model call goes through one gateway that streams the reply and records the provider,
model, tokens, time to the first token, total time and an estimated cost for each attempt.
Models are chosen per kind of work (a role) as `provider:model`, where the provider is `groq`,
`openrouter` or `mistral`:

- `EBB_TEACH_MODEL`: lessons and the learn phase's answers. Default `groq:openai/gpt-oss-120b`.
- `EBB_QUIZ_MODEL`: writing questions, grading and re-grading. Same default.
- `EBB_EVAL_WRITER_MODEL`: the model `ebb eval build` writes cases with. Default
  `mistral:mistral-medium-latest`; it must differ from the quiz model, which it is checked
  against.
- `EBB_TEACH_BACKUP`, `EBB_QUIZ_BACKUP`: the model tried when the main one fails (default:
  Mistral, when `MISTRAL_API_KEY` is set).
- `OPENROUTER_API_KEY`: needed when any role names an `openrouter:` model (keys at
  https://openrouter.ai/keys). OpenRouter serves many models through one key.
- `EBB_PRIMARY_MODEL`, `EBB_FALLBACK_MODEL`: the older settings, a bare model name (primary on
  Groq, fallback on Mistral). They still work, as the defaults for the settings above.
- `EBB_SHOW_COSTS` (default `true`): shows the Costs page and its header link.

### The Costs page (dev only)

With `EBB_SHOW_COSTS` on, the header has a "Costs" link (marked "dev only") to a page of totals,
a tokens-per-day chart, per-role and per-model health, a table of the latest calls and
anomalies (for example a call far slower than that endpoint's usual). It will be hidden in
milestone 6. Costs are **estimates for a paid plan**: tokens times the price per million that the
model's provider lists. Ebb uses free plans today, so what you really pay is $0; a model on a free
plan is labelled as such. Only Groq `openai/gpt-oss-120b` has a price, read from Groq's page (its
source and date are on the Costs page). A model with no recorded price, such as Mistral's
`-latest` models (no Mistral page says which version the alias serves), shows "price unknown"
rather than a guess, and a total that includes one says so.

### The learn phase

The first time you study a concept, the website opens a lesson instead of a question. It is
written in the `/teach-me` format from your notes plus a web search (Tavily, when its key is
set), streams in as it is written, cites its sources and says when something is not in your
notes. It ends with a few "Check yourself" questions that Ebb leaves unanswered until you press
"Answer this for me". You can also ask your own questions on the same page, or tap a "Suggested
next" chip. When you are ready, "I'm ready, quiz me" starts the quiz, which begins with multiple
choice. What you ask in the learn phase never changes your tide or review schedule. A learned
concept's lesson stays under "Read the lesson again" in the left column. The learn phase is on
the website only; the terminal app is unchanged and still starts a concept with a question.

### Add your notes

Put PDF, TXT or MD files in the `notes/` folder at the repo root (Docker sees it as
`notes/`), then:

```bash
docker compose run --rm ebb new "Computer Networks" --notes notes/lecture1.pdf   # a subject from notes
docker compose run --rm ebb add notes/lecture2.pdf "Computer Networks"           # more notes later
docker compose run --rm ebb status "Computer Networks"                           # lists its sources
```

On the website, add notes while creating a subject or later with "Upload notes". Without Docker,
pass any path (`uv run ebb add ~/lecture2.pdf`). Limits: PDF, TXT or MD,
20 MB per file, 5 files per subject; scanned (image-only) PDFs are refused, so export a text
PDF. Don't upload sensitive documents: the files' text is sent to an AI model (Groq, or
Mistral when Groq fails) to find concepts, write questions and cite answers.

Questions drawn from your notes show a citation with a quote and page. If a question cannot be
tied to your notes, it is marked as general knowledge.

### Disputes

If a short-answer grade looks wrong, press "Dispute grade" on the website, or type `d` at the
next prompt in the terminal. Pick a
reason, optionally explain, and Ebb re-checks the answer with a stricter grader. The result can
change the grade, void the answer (it stops counting) or uphold it.

### Grading eval

How often does the grader agree with the label of a case? From `backend/`:

```bash
cd backend
uv run ebb eval build    # a writer model (Mistral) generates labelled cases; --force to rebuild
uv run ebb eval run      # the primary model (Groq) grades them; writes a dated report
```

The labels are generated by a model, not written by a person, so the score measures agreement
with that generator, not with a teacher. The target is 90%. Reports are committed under
`backend/evals/results/`. Both commands accept `--dataset`; `eval build` also takes `--notes`
and `--force`, and `eval run` also `--results`. `eval run` retries a case that fails on a rate
limit or an overloaded provider, and counts cases that still fail apart from disagreements.

The committed set (`backend/evals/grading.jsonl`, 50 cases) was written by Groq
`qwen/qwen3.8-27b`, because Mistral, the intended writer, isn't active on the owner's account
yet. Groq `openai/gpt-oss-120b` graded it: the first baseline is **70% (35 of 50)**. Rebuilding
the set needs `MISTRAL_API_KEY`.

For development, with [uv](https://docs.astral.sh/uv/):

```bash
docker compose up -d db           # only the database: the API runs from uv below
cd backend
uv sync
uv run pytest                     # database tests need the compose db
uv run ebb study
uv run ebb serve                  # the API on port 8000; then, in frontend/: npm install && npm run dev
```

The website's checks run from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, and
`npm run e2e`, a browser test (Playwright) of onboarding, a lesson, the quiz, the Costs page and
the progress page against a fake tutor, so it needs no database or API key. The first time,
install its browser with `npx playwright install chromium`.

**Try spaced review now:** pretend a week has passed, so every concept whose next review
was within the next 7 days becomes due. The same statement moves the answer history and the
sessions back too, so the progress dashboard and the streak agree:

```bash
# Pretend a week has passed: reviews due within the next 7 days become due now
docker compose exec db psql -U ebb -d ebb -c \
  "update concepts set last_reviewed_at = last_reviewed_at - interval '7 days', next_review_at = next_review_at - interval '7 days' where stage > 0; update attempts set graded_at = graded_at - interval '7 days'; update study_sessions set started_at = started_at - interval '7 days', last_active_at = last_active_at - interval '7 days', ended_at = ended_at - interval '7 days'"
```

To see a web question, answer every question of a concept, then study that concept again in
a later session with `TAVILY_API_KEY` set: when the newly written questions repeat ones you
already answered, Ebb searches and swaps them out. It depends on what the model writes, so it
is not guaranteed on the first try.

## How it works

`ebb study` runs a LangGraph graph: pick the concept most in need of review,
generate a batch of questions, ask one (the graph pauses with `interrupt`),
grade it (multiple choice in code, short answers by the LLM), update the
concept's Leitner stage, then route: teach on a miss, a harder question after a
correct answer, the next concept after two correct in a row. Every routing and
scheduling decision is plain Python with tests; the LLM only writes, grades and
teaches.

Spaced review comes first: `ebb study` greets with how many concepts are due and
reviews the most faded first, then teaches new ones. If the terminal closes or Ebb
crashes mid-session, the next `ebb study` within 24 hours offers to resume at the
same question.

A session runs until you type q. When every concept is learned and nothing is due,
Ebb asks the tutor for more advanced concepts and carries on with those. It never
repeats a question you have already answered.

## What's here

| File | What it is |
|---|---|
| [Ebb — Requirements & System Design.pdf](Ebb%20—%20Requirements%20&%20System%20Design.pdf) | Requirements, screens, agent graph, database schema and build milestones |
| [ebb-prototype.html](ebb-prototype.html) | Clickable prototype of every screen and system state, with mock data (open in a browser) |
| [docs/plan-reviews.md](docs/plan-reviews.md) | Design and engineering reviews of the plan, with the amendments they made |

## Planned stack

LangGraph + LangChain · Groq (primary) with a Mistral fallback · Tavily web search ·
FastAPI · Supabase Postgres, Auth and Storage · Postgres full-text search · Next.js with
CSS Modules (fonts: Newsreader and IBM Plex, as in the prototype). Free tiers throughout.
