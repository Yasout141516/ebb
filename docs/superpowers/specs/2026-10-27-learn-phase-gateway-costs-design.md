# Before milestone 5: the LLM gateway, the cost dashboard and the learn phase (design)

Date: 2026-10-27 · Status: awaiting owner approval

Builds three things the owner asked for after using the milestone 4 web app
([spec](2026-10-23-web-app-design.md)), before milestone 5 starts, plus the fixes from
[m4-followups.md](../m4-followups.md) ("Found in use", 2026-10-27). The screens are drawn on
the design canvas <https://claude.ai/artifact/QsrFjBQvCGT5xE1FwuXn2a> (private to the owner),
in the style of [ebb-prototype.html](../../../ebb-prototype.html). Where this document
disagrees with the milestone 4 spec, this document wins.

## Intent

The owner studied with Ebb for the first time and found three gaps.

1. **Ebb quizzes before it teaches.** A new concept starts with a question; a lesson only
   follows a wrong answer, and it is at most 120 words. The owner wants to be taught first,
   in depth, the way their own `/teach-me` skill teaches
   (`~/.claude/skills/teach-me/SKILL.md`), with the web searched for more than the notes
   hold, and to ask questions about the lesson before being quizzed. Quizzing stays its own
   step, entered when the learner says they're ready.
2. **Nobody knows what a call costs or why Ebb is slow.** Ebb throws away the token counts
   every provider returns and never times a call. The owner wants every call's tokens, time
   to first token (TTFT), total time and estimated cost, per request, endpoint and tenant,
   with anomalies flagged, on a page of the app.
3. **Ebb leans on one Groq model for everything.** The owner wants a small gateway of their
   own inside Ebb that routes each kind of work to its own model: Groq for teaching, one
   model (from OpenRouter, chosen later) for quizzing and grading.

And one bug: the correct multiple-choice option is almost always option 1, because Ebb keeps
the options in the order the model wrote them.

The project is a portfolio piece about agent engineering. The gateway and the dashboard are
the "operating an LLM system" half of that story; the learn phase is a second LangGraph graph
next to the quiz graph.

## Goals

1. **Shuffled options.** The correct option lands in each of the four positions about
   equally often.
2. **One gateway for every model call.** Each call goes through `ebb.llm` to the model its
   *role* is configured with, streams from the provider, and leaves one record per attempt:
   tokens in and out, TTFT, total time, outcome, whether it was a retry or a fallback, and
   estimated cost.
3. **Roles in config.** Teaching, quizzing-and-grading and eval-writing each name a
   provider and model in `.env`. Changing one is a config change, not a code change.
   OpenRouter is a supported provider.
4. **A Costs page** (`/costs`, marked "dev only") showing cost and tokens per call, per
   endpoint and per tenant, speed by model, the gateway's role table, and anomalies.
5. **The learn phase.** The first time a concept comes up in the web app, Ebb teaches it
   in the `/teach-me` format from the notes and a web search, answers the learner's
   questions about it on the same page, and starts the quiz on that concept when the learner
   presses **I'm ready, quiz me**.

## Non-goals

- **Switching models when one is slow** (owner: later). The gateway records the numbers
  that rule will need; it switches only on errors, as today.
- **Choosing the OpenRouter model.** The owner will provide candidates. Until then the
  quizzing-and-grading role points at Groq `openai/gpt-oss-120b`, as today.
- **Token-saving changes** (lower reasoning effort, fewer questions per call, a shorter
  avoid list, fewer validation retries, a smaller model for easy tasks). They wait for the
  dashboard's real numbers. Listed in [m4-followups.md](../m4-followups.md).
- **OmniRoute** or any external gateway.
- **The full Chat mode.** Questions in the learn phase are about that one concept only.
- **The learn phase in the CLI.** `ebb study` keeps quizzing new concepts straight away.
- **Quiz questions written from the lesson's web pages.** The quiz uses the notes (and its
  existing web-question mode) as today.
- **Per-tenant anything beyond grouping.** One user until sign-in (milestone 6).
- Milestone 5's items (flashcards, exam mode, PDF export, resource search).

## Anti-goals (failures even if everything "works")

- **Recording that breaks studying.** If writing a call record fails, the learner's call
  still succeeds; the failure is logged, not raised.
- **A dashboard that lies.** A cost shown for a model with no known price, a TTFT shown for
  a call that didn't stream, or tokens invented when the provider reported none. Unknown is
  shown as "not reported" or "price unknown".
- **The learn phase changing memory.** Reading the lesson and asking questions never moves
  a concept's tide, stage or review date. Only quiz answers do.
- **A citation chip with no verified quote**, in lessons and answers as in the quiz.
- **Paying twice for the same lesson.** A lesson is written once per concept and stored;
  opening it again doesn't call a model.
- **Model output treated as instructions or as HTML.** Lesson markdown is rendered without
  raw HTML; web pages and notes stay fenced in prompts, as today.

## Decisions made by the owner

| Topic | Decision |
|---|---|
| Order | All of this before milestone 5 |
| Gateway | Ebb's own, a module inside the backend; not OmniRoute |
| Roles | Groq `gpt-oss-120b` for teaching; one model for quizzing **and** grading (OpenRouter, chosen later); the eval writer is never the grader |
| Switching when slow | Later |
| Streaming | Model calls stream, so TTFT can be measured |
| Dashboard | Token cost breakdown, TTFT "and every other important detail", per request, endpoint and tenant, anomaly detection; its own page, hidden later |
| Lesson format | The owner's `/teach-me` skill |
| Web search | A LangGraph graph searches the web so teaching goes deeper than the notes; Tavily, already in Ebb |
| Learn-phase questions | Check-yourself questions are unanswered until the learner asks; Ebb suggests follow-up questions and answers them; the learner can type their own. All on the lesson's page |
| Quizzing | A separate step, after **I'm ready, quiz me**, multiple choice first |
| Look | The prototype's style; screens on the design canvas |
| Word-by-word text | In the learn phase only (the lesson and Ebb's answers there). Quiz cards stay whole, as milestone 4 decided |
| Learn-phase questions and answers | Kept with the lesson: **Read the lesson again** shows both |
| A lesson or answer that breaks after text has appeared | The shown text is wiped and the error card appears with **Retry**; no automatic rewrite on screen. Before any text has appeared, retries and the backup model work silently as today. Nothing partial is stored |

## Behaviour

### Shuffled options

After a question batch is validated, each multiple-choice question's four options are put in
a random order before the question is saved or shown. Grading by number reads the saved
order, so nothing else changes. The CLI and the website both get it.

### The learn phase (web app)

**When it happens.** In the web app, when the quiz session picks a concept that has never
been answered (stage 0, no answers) and has not been learned yet, the session pauses and the
thread shows that concept's lesson instead of a question. Concepts that were already
studied before this change (they have answers) are never sent through it. A concept the
learner finished learning (pressed Ready) goes straight to its quiz next time.

**The lesson.** One card at the top of the concept's part of the thread
(canvas: "Study: learn phase"):

- A label "Lesson · quiz opens when you press Ready" and the sources it was written from
  ("Your notes · pp. 12 to 15", "2 web sources").
- The lesson in the `/teach-me` format, scaled to the concept:
  1. The concept's name as the title.
  2. **In one sentence** (TL;DR), for non-trivial concepts.
  3. **Built from the ground up**: first principles in plain language.
  4. **The simple picture**: one analogy, one paragraph, not a restatement of 3.
  5. **Seeing it work**: a worked example; a code block for code topics; a table where
     steps or attributes line up.
  6. **Depth and traps**: edge cases and misconceptions, as a table when they compare.
  7. **Check yourself**: 2 to 4 questions, **not answered**, each with an
     **Answer this for me** button.
  A simple concept gets only 3 and 5, without headings, as the skill says.
- Markdown is rendered: headings, bold, lists, tables, code. No raw HTML.
- Facts from a source carry citation chips, verified like the quiz's (notes) or listed as
  web pages (title and domain). Text with no source is marked "Not in your notes" when the
  subject has notes.
- **Streaming on screen:** see *Calls I made*, item 1.

**Questions on the same page.** Below the lesson, the thread continues:

- **Answer this for me** on a check-yourself question posts it as the learner's bubble and
  Ebb answers it.
- **Suggested next**: 2 or 3 follow-up questions as chips, written with each answer
  (and with the lesson). Tapping one asks it.
- The **composer** takes the learner's own question about the concept (placeholder "Ask
  your own question about ⟨concept⟩"). Questions about something else are answered briefly
  and steered back (the prompt says so; code does not police it).
- Each answer is plain prose with the same citation rules as the lesson, shown under the
  question.
- None of this is graded or recorded as an answer.

**Ready.** A card under the thread: "Ready to be quizzed? Ebb starts with multiple choice.
Questions you asked here do not change your tide or your review schedule." with
**I'm ready, quiz me**. Pressing it marks the concept learned and the quiz session continues
on that concept as today, except that the first question is multiple choice when the batch
has one. The lesson stays in the thread above.

**Coming back.** The lesson is stored per concept. Leaving and coming back (refresh, other
tab, next day) shows the stored lesson without a model call. The questions and answers of
an unfinished learn phase are kept like a quiz session's thread (the tab's session storage),
and the server keeps them in the learn graph's checkpoint, so a resumed learn phase shows
them again. A learned concept's lesson can be reopened from the left column
(**Read the lesson again**), read-only, with no new model call.

**Memory panel.** A concept in its learn phase shows "learning · not quizzed yet". The tide
starts at its first quiz answer.

**No mode chips.** The canvas draws Learn/Quiz/Flashcards/Exam/Chat chips over the
composer; build without them (Chat stays dropped, Flashcards and Exam stay hidden until
milestone 5).

**Without Tavily, without notes.** No `TAVILY_API_KEY`: the lesson is written from the
notes alone, and the label says "Your notes only". No notes either: from general
knowledge, labelled "General knowledge · not from your notes". A failed search is a notice
("Web search unavailable — this lesson uses your notes only."), never an error.

**Failures.** A lesson or answer that fails after retries shows the existing error card
with **Retry**; the daily AI-call cap and refusals behave as in the quiz. Every learn-phase
model call counts against the daily cap.

### The gateway

Every model call Ebb makes (quiz, learn phase, onboarding, disputes, evals; website and CLI)
goes through one place in `ebb.llm`. For each call:

1. **Pick the role's chain.** The method decides the role:

   | Role | Calls |
   |---|---|
   | `teach` | the lesson, learn-phase answers, the re-teach after a wrong answer |
   | `quiz` | writing and rewriting questions, grading, re-grading, finding concepts (topic, notes, extending a subject) |
   | `eval_writer` | writing eval cases |

   Each role has a main model and, optionally, a backup, from settings (names in
   *Configuration*). The backup is used only after the main model fails, as the Mistral
   fallback is today.
2. **Stream from the provider**, keeping today's behaviour: structured replies are
   validated, invalid ones retried, refusals end the call, a bad key or model name is a
   configuration error.
3. **Record every attempt** (a retry or a fallback is its own row, grouped under one call):
   when, who (tenant), which request it served, role, endpoint, provider, model, attempt
   number, outcome (`ok`, `invalid` (failed validation), `error`, `refused`), input tokens,
   output tokens and reasoning tokens as the provider reports them (or none), TTFT and total
   time in milliseconds, and the estimated cost.

**Endpoint** is the kind of work: `lesson`, `learn_answer`, `write_questions` (and
`write_web_questions`, `rewrite_questions`), `grade`, `regrade`, `reteach`,
`extract_concepts`, `extend_concepts`, `concepts_from_notes`, `write_eval_case`.

**Request** groups the calls one user action caused: one quiz turn, one learn-phase turn,
one uploaded file, one CLI step, one eval case. "This answer cost $0.0006 across 3 calls."

**Estimated cost** = tokens × the model's price per million tokens, from a price table in
the code with each price's source and date. A model with no price records no cost and shows
"price unknown". Free tiers cost $0 in fact; the page shows what the calls would cost on a
paid plan, and says so.

### The Costs page

A **Costs** link in the header with a "dev only" tag, at `/costs`, shown while the setting
`EBB_SHOW_COSTS` is on (default on; milestone 6 turns it off). Layout as on the canvas
("Costs and gateway"), in the Progress page's style:

- **Filters:** range (24 hours, 7 days, 30 days), tenant, endpoint, model.
- **Anomalies** banner, when any: one line each, in words ("Grade answer at 14:02 used 4.1
  times the input tokens a grading call usually uses").
- **Summary cards:** estimated cost on a paid plan (and "Real spend: $0.00 on free tiers"
  when every model used is on a free tier — see *Left to the builder*), tokens in/out, calls
  (retried, fell back), TTFT p50/p95, total time p50/p95, fallback rate, retry rate, daily
  call limit used today.
- **Estimated cost by feature** (bars) and **tokens per day** (columns; an anomalous day
  flagged).
- **Gateway: who handles what**: each role's main and backup model, recent health (share of
  failed attempts) and p95 total time. The canvas's "Switches when" column is left out
  (switching on slowness is a non-goal).
- **Speed and reliability by model**: calls, TTFT p50/p95, total p50/p95, output tokens per
  second, fallbacks.
- **By endpoint**: calls, tokens in/out, estimated cost, cost per call.
- **By tenant**: calls and cost per user (one row today).
- **Recent calls**: the last 50 attempts, newest first, anomalies shaded and named in the
  status column; a request's attempts can be expanded together.
- **Where the prices come from**: each model's price, its source and date.

**Anomaly rules** (code, not a model; computed when the page is read):
- A call whose input or output tokens are more than 3 times the median of the previous 50
  `ok` calls of the same endpoint (at least 10 of them).
- A call whose TTFT is more than 3 times that endpoint's median and over 2 seconds; or
  whose total time is more than 3 times the median and over 5 seconds.
- A day whose tokens are more than twice the mean of the 7 days before it (at least 3 days
  with calls).
- Failed attempts (`error`, `refused`, all providers failed) are listed as failures, not
  anomalies.

**Empty state:** "No model calls yet in this range."

## Technical design

### Shape

```
web app ──► API ──► quiz graph (existing) ──┐
                 └► learn graph (new)  ─────┤
CLI ────────────────────────────────────────┤
                                            ▼
                         Tutor protocol (unchanged signatures, + learn methods)
                                            ▼
                         gateway: role → chain → stream → record
                                            ▼
                         Groq · OpenRouter · Mistral        llm_calls table
```

- The `Tutor` protocol stays the graph's only view of models. `LangChainTutor` keeps its
  prompt-building; its `_run` becomes the gateway's call, given a role and an endpoint.
  `CappedTutor` keeps counting each method call against the daily cap.
- Providers: Groq (`langchain-groq`), Mistral (`langchain-mistralai`), OpenRouter through
  its OpenAI-compatible API (`langchain-openai`, a new dependency). Each needs its key only
  when a role uses it.
- Timing comes from the provider stream: TTFT is the time to the first streamed chunk.
  Usage comes from the provider's usage report on the stream (enable it where the client
  needs a flag); when a provider reports none, the row says so.
- The request id travels in a context variable set by the API's session service, the learn
  service, onboarding and the CLI, so the gateway needs no new parameters.
- Writing a record goes through the `Store` (`record_llm_call`), in its own short
  transaction, after the attempt; failures are logged and swallowed.

### The learn graph

A second LangGraph graph with its own checkpoint thread per (user, concept):

```
gather_sources ─► write_lesson ─► verify_citations ─► wait (interrupt)
                                                         │
          ask (question) ─► gather_sources ─► answer ─► verify ─► wait
          ready ─► mark_learned ─► END
```

- `gather_sources`: the notes' chunks for the concept (existing retrieval) and one Tavily
  search (query from the concept and subject; for an answer, from the question). Skipped
  without a key. A failed search is a notice.
- `write_lesson` (role `teach`, endpoint `lesson`) and `answer` (endpoint `learn_answer`)
  produce markdown plus, after it, citations (notes: `{source, quote}` verified by the
  existing citation code; web: page URL) and 2 or 3 suggested follow-ups. One way to keep
  the text streamable: the model writes the markdown, then a fenced trailer the code
  parses; the screen stops showing text at the trailer. The builder may choose another way
  that keeps the anti-goals.
- Citation retries as in the quiz (`retry.py`); an uncitable claim loses its chip, never
  the text.
- `wait` interrupts with the thread so far; replies are `{ask: text}` or `{ready: true}`.
- Prompts: `learn_lesson.md` (the `/teach-me` rules, fenced sources, scaling rule, "do not
  answer the check-yourself questions") and `learn_answer.md`. Length: see *Calls I made*,
  item 2.

**Hooking it to the quiz.** The quiz graph gets one new check after `pick_concept`: when the
session was started with the learn phase on (the web app always; the CLI never) and the
picked concept is new and not learned, it interrupts with a `learn` payload (concept id and
name) instead of going to `retrieve_notes`. The web app then runs the learn graph for that
concept on the same page; on **Ready** the learn graph marks the concept learned and the
web app replies `{learned: true}` to the quiz session, which continues to
`retrieve_notes` as today. The quiz's routing rules are otherwise untouched.

### Schema: `005_…sql`

- `llm_calls`: one row per attempt: id, call id, request id, user id, at, role, endpoint,
  provider, model, attempt, outcome, input/output/reasoning tokens (nullable), TTFT ms
  (nullable), total ms, estimated cost in USD (nullable), error kind (nullable). Indexed by
  (user, at) and (endpoint, at).
- `concept_lessons`: per concept: lesson markdown, its citations and web pages, suggested
  follow-ups, model, written at, learned at (nullable). One row per concept.
- `lesson_exchanges`: per concept, in order: the question (and whether it was a
  check-yourself, a suggestion or typed), Ebb's answer, its citations and suggestions,
  asked at. Kept after Ready, so **Read the lesson again** shows them; the learn graph's
  checkpoint can then be deleted when the concept is learned.
- No backfill; calls before the migration have no records.

### API (sketch; exact routes are the builder's)

- `POST /concepts/{id}/learn` (start or resume, SSE: steps, streamed text, then the turn),
  `POST /learn/{thread}/reply` (`ask` or `ready`, SSE), `GET /concepts/{id}/lesson`
  (stored lesson, read-only).
- `GET /costs/summary`, `GET /costs/calls`, `GET /costs/prices` with the filters as query
  parameters. Each refuses with 404 while `EBB_SHOW_COSTS` is off.
- The quiz SSE gains the `learn` turn type.

### Frontend

- New `Lesson` thread card and learn-phase cards (question bubble, answer with chips,
  suggestions, Ready card), in `components/study/`, styled from `styles/tokens.css` as CSS
  Modules like the rest.
- Markdown via `react-markdown` with `remark-gfm` (tables), raw HTML off. *(My call; new
  dependencies.)*
- `/costs` page modelled on `app/progress/[id]/page.tsx` (loading, error, narrow layout);
  a `Costs` link in `Header.tsx` while the API says the page is on.

### Configuration (`.env`)

| Setting | Default |
|---|---|
| `EBB_TEACH_MODEL` | `groq:openai/gpt-oss-120b` |
| `EBB_QUIZ_MODEL` | `groq:openai/gpt-oss-120b` (owner will give an OpenRouter model) |
| `EBB_EVAL_WRITER_MODEL` | `mistral:mistral-medium-latest` |
| `EBB_TEACH_BACKUP`, `EBB_QUIZ_BACKUP` | unset (today's Mistral fallback when `MISTRAL_API_KEY` is set) |
| `OPENROUTER_API_KEY` | unset |
| `EBB_SHOW_COSTS` | `true` |

`EBB_PRIMARY_MODEL` and `EBB_FALLBACK_MODEL` keep working as the defaults of the new
settings for one milestone, so an existing `.env` doesn't break. The eval writer must not
be the same model as `EBB_QUIZ_MODEL`; `ebb eval build` refuses if it is.

## Errors

- Missing key for a role's provider: the existing configuration-error path ("OPENROUTER_API_KEY
  is not set — EBB_QUIZ_MODEL uses OpenRouter").
- A record that can't be written: logged at warning; the call's result is returned.
- Learn graph failures: the existing error card and notices; the quiz session stays paused
  on the `learn` turn until the learner retries or ends the session.
- A stream that breaks (or a trailer that won't parse, or a refusal) after text has reached
  the screen: the API sends an event telling the page to discard that text, then the error
  card with **Retry** (owner's decision). Retry makes a fresh call through the gateway,
  backup included.
- Costs page with the database down: the existing "Can't reach the database" message.

## Testing

- Shuffle: over many generated batches the correct option appears in every position;
  with a seeded random the order is reproducible. Existing tests that assume option 1 is
  correct (CLI tests, the e2e spec, the fake tutor) are updated to find the correct option.
- Gateway: fake streaming chat models give exact TTFT, totals, usage and outcomes; a retry
  and a fallback each write their own row; a failing record write doesn't fail the call;
  missing usage stays null; unknown price gives null cost.
- Anomaly rules: pure functions with fixed inputs.
- Learn graph: with the fake tutor and a fake search: lesson, ask, ready, resume from the
  checkpoint, no-key and failed-search paths, citation retry, and that tide/stage are
  unchanged afterwards.
- Quiz hook: a new concept interrupts with `learn` only when the learn phase is on; the CLI
  path is unchanged.
- API: SSE event order for learn turns; `/costs` 404 when off.
- Frontend: Vitest for the new cards, markdown (tables render, raw HTML doesn't), and the
  Costs page states; the Playwright e2e grows a learn phase before the quiz.

## Rollout

The first API start migrates `ebb` to `005`. Back it up first, as the README describes, as
`ebb_pre005`. Nothing is backfilled; the Costs page starts empty.

## Done when

1. In the web app, a new concept opens with a lesson in the `/teach-me` format, built from
   the notes and the web, with verified chips; check-yourself answers, suggested follow-ups
   and typed questions work on the same page; **I'm ready, quiz me** starts the quiz on it;
   the tide only moves on quiz answers.
2. Reopening a learned concept's lesson makes no model call.
3. The correct option's position is shuffled.
4. Every model call from the website and the CLI leaves records with tokens, TTFT, total
   time, outcome and estimated cost (or "unknown").
5. Changing `EBB_QUIZ_MODEL` to an OpenRouter model with `OPENROUTER_API_KEY` set routes
   quizzing and grading there and nothing else.
6. `/costs` shows every section above from real records, with anomalies by the rules.
7. All existing tests pass; the new ones above exist; the e2e covers learn → quiz.
8. README and HANDOFF describe the new settings, the Costs page and the learn phase.

## Calls I made (check these)

1. *(Now the owner's decision, see the table.)* Citation chips appear when the text is done.
2. **Lesson length:** no hard limit, as the skill says, but the prompt aims at about 600 to
   1,200 words for a deep concept, to keep cost and wait reasonable.
3. **One web search per lesson and per answer**, skipped without a key.
4. **The learn phase is web-only**; the CLI is unchanged.
5. **Concepts studied before this change skip the learn phase.**
6. **The first quiz question after Ready is multiple choice** when the batch has one.
7. **Read the lesson again** for learned concepts, from the stored copy.
8. **Every attempt is a row**, so retries and fallbacks show their own cost and time.
9. **`react-markdown` + `remark-gfm`** for rendering.
10. **The canvas's mode chips and "Switches when" column are left out.**
11. **Citation chips sit in one row under each card** (the lesson, each answer), as the
    quiz's lesson card does today, not inline per fact. "Not in your notes" is one badge on
    a lesson or answer that has no verified notes citation, when the subject has notes.
    The lesson's header line ("Your notes · pp. 12 to 15", "2 web sources") summarises the
    sources it was written from.
12. **Web citations are verified quotes**, like the quiz's (`{source, quote}` checked
    against the page text by the existing citation code); one that fails is dropped after
    the citation retries. The anti-goal "no chip without a verified quote" covers them.
13. **The learn phase is fixed per session when it starts.** A session started from the
    web app has it on; one started by `ebb study` has it off. When `ebb study` resumes a
    session paused on a `learn` turn, it skips the lesson (the concept stays not learned)
    and quizzes as today. A CLI-started session resumed in the browser gives no lessons;
    new concepts in the next web session do.

## Left to the builder

- The exact way streamed text and its citations/follow-ups are separated.
- How to stream structured (JSON) replies while keeping today's validation and retries.
- The price table's numbers (from each provider's own pricing page, with date), and how
  "on a free tier" is known per provider.
- The learn graph's checkpoint thread naming and how it resumes.
- Route names, SSE event names, component names.
- Query shape and indexes for the Costs page; computing percentiles in SQL or Python.
- Whether the Costs page refreshes itself.
