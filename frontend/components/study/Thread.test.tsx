import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, getJson, postJson } from "@/lib/api";
import { saveThread, rememberThread, type Card } from "@/lib/session";
import { CUT_OFF, streamPost, type SseEvent } from "@/lib/sse";
import type { Exchange, Finished, LearnPayload, LearnTurn, QuizTurn, StoredLesson, TurnPayload } from "@/lib/types";
import Thread from "./Thread";
import useStudy from "./useStudy";
import Workspace from "./Workspace";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
  postJson: vi.fn(),
}));
vi.mock("@/lib/sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sse")>()),
  streamPost: vi.fn(),
}));

type Stream = {
  path: string;
  body: Record<string, unknown>;
  emit: (event: string, data: unknown) => void;
  end: () => Promise<void>;
  fail: (error: unknown) => Promise<void>;
};
let streams: Stream[];

const EXHAUSTED = "The tutor couldn't respond — your progress is saved. Try again in a minute.";

function Harness({ onDispute = () => {} }: { onDispute?: (id: string) => void }) {
  const study = useStudy("s1");
  return <Thread study={study} onDispute={onDispute} />;
}

const mcq = (over: Partial<TurnPayload> = {}): TurnPayload => ({
  concept: "Subnetting",
  number: 1,
  question: {
    type: "mcq",
    difficulty: "medium",
    prompt: "How many usable host addresses does a /26 have?",
    options: ["64", "62", "30", "126"],
    source: "notes",
    source_line: "From your notes · p. 21",
  },
  feedback: null,
  lesson: null,
  notices: [],
  can_dispute: false,
  dispute_result: null,
  ...over,
});

const short = (over: Partial<TurnPayload> = {}): TurnPayload =>
  mcq({
    concept: "DNS resolution",
    question: {
      type: "short",
      difficulty: "medium",
      prompt: "Why do resolvers cache answers?",
      options: [],
      source: "notes",
      source_line: null,
    },
    ...over,
  });

const finished: Finished = {
  results: [],
  final: { grade: null, lesson: null, notices: [] },
  caught_up: false,
  next_review_at: null,
  next_review_line: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("ebb-driver", "drv-1");
  streams = [];
  // The session's offer; the side columns' lists (Workspace) are empty.
  vi.mocked(getJson).mockImplementation(async (path: string) => {
    if (path.startsWith("/subjects/s1/session")) return { offer: null };
    if (path === "/subjects/s1/concepts") return { concepts: [], due_count: 0, next_review_at: null };
    return [];
  });
  vi.mocked(postJson).mockResolvedValue(undefined);
  vi.mocked(streamPost).mockImplementation(
    (path, body, onEvent: (e: SseEvent) => void) =>
      new Promise<void>((resolve, reject) => {
        streams.push({
          path,
          body: body as Record<string, unknown>,
          emit: (event, data) => act(() => onEvent({ event, data })),
          end: () => act(async () => resolve()),
          fail: (error) => act(async () => reject(error)),
        });
      }),
  );
});

const lastStream = async () => {
  await waitFor(() => expect(streams.length).toBeGreaterThan(0));
  return streams[streams.length - 1];
};

async function startedAt(turn: QuizTurn) {
  render(<Harness />);
  const stream = await lastStream();
  stream.emit("started", { thread_id: "t1", greeting: "Welcome back — 3 ideas are ebbing. Reviewing those first." });
  stream.emit("turn", { payload: turn });
  await stream.end();
  return stream;
}

describe("Thread", () => {
  it("starts a session with the greeting when there is nothing to resume", async () => {
    render(<Harness />);
    const stream = await lastStream();
    expect(getJson).toHaveBeenCalledWith("/subjects/s1/session?driver=drv-1", expect.anything());
    expect(stream.path).toBe("/subjects/s1/session");
    expect(stream.body).toEqual({ driver: "drv-1" });
    stream.emit("started", { thread_id: "t1", greeting: "Nothing is due. Let's learn something new." });
    expect(screen.getByText("Nothing is due. Let's learn something new.")).toBeInTheDocument();
  });

  it("shows each step until the next turn replaces it", async () => {
    render(<Harness />);
    const stream = await lastStream();
    stream.emit("started", { thread_id: "t1", greeting: "Hello." });
    stream.emit("step", { label: "Reading your notes…" });
    expect(screen.getByText(/Reading your notes…/)).toBeInTheDocument();
    stream.emit("step", { label: "Writing questions…" });
    expect(screen.queryByText(/Reading your notes…/)).not.toBeInTheDocument();
    expect(screen.getByText(/Writing questions…/)).toBeInTheDocument();
    stream.emit("turn", { payload: mcq() });
    expect(screen.queryByText(/Writing questions…/)).not.toBeInTheDocument();
    expect(screen.getByText("Multiple choice · Subnetting · Q1")).toBeInTheDocument();
  });

  it("keeps an MCQ answer locked in its card, and Retry re-sends the same attempt", async () => {
    await startedAt(mcq());
    await userEvent.keyboard("2{Enter}");
    const reply = await lastStream();
    expect(reply.path).toBe("/sessions/t1/reply");
    const sent = reply.body as { driver: string; reply: { attempt_id: string; answer: string } };
    expect(sent.driver).toBe("drv-1");
    expect(sent.reply.answer).toBe("2");
    expect(sent.reply.attempt_id).toMatch(/^[0-9a-f-]{36}$/);
    const chosen = screen.getByRole("radio", { name: "62, your answer" });
    expect(chosen).toBeChecked();
    expect(chosen).toBeDisabled();

    await reply.fail(new ApiError(200, CUT_OFF));
    expect(screen.getByRole("alert")).toHaveTextContent(CUT_OFF);
    expect(screen.getByRole("button", { name: "End session" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    const again = await lastStream();
    expect(streams).toHaveLength(3);
    expect(again.body).toEqual(sent);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a failure in the stream as the error card with Retry, whatever its words", async () => {
    await startedAt(short());
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    const down = "Can't reach the database. Is it running? Try: docker compose up -d";
    reply.emit("error", { notice: down });
    await reply.end();
    expect(screen.getByRole("alert")).toHaveTextContent(down);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect((await lastStream()).body).toEqual(reply.body);
  });

  it("shows the busy card when another tab took the session over", async () => {
    await startedAt(mcq());
    await userEvent.keyboard("1{Enter}");
    const reply = await lastStream();
    await reply.fail(new ApiError(409, "That session is open in another tab or terminal."));
    expect(screen.getByText("That session is open in another tab or terminal.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End session" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start fresh" }));
    expect(postJson).toHaveBeenCalledWith("/sessions/t1/discard", { driver: "drv-1" }, expect.anything());
    expect((await lastStream()).path).toBe("/subjects/s1/session");
  });

  it("gives a refused answer back to its question, without a Retry that can't succeed", async () => {
    await startedAt(short());
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    await reply.fail(new ApiError(422, "Something about the answer."));
    expect(screen.getByRole("alert")).toHaveTextContent("Something about the answer.");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByText("Your answer", { selector: "span" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toBeEnabled();
  });

  it("shows a configuration error full width", async () => {
    render(<Workspace subjectId="s1" onBackup={() => {}} />);
    const stream = await lastStream();
    stream.emit("error", { error: "GROQ_API_KEY is not set. Add it to .env." });
    await stream.end();
    expect(screen.getByText("GROQ_API_KEY is not set. Add it to .env.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("main")).not.toBeInTheDocument();
  });

  it("tells the header once the tutor switched to its backup model", async () => {
    const onBackup = vi.fn();
    render(<Workspace subjectId="s1" onBackup={onBackup} />);
    const stream = await lastStream();
    stream.emit("started", { thread_id: "t1", greeting: "Hello." });
    expect(onBackup).toHaveBeenLastCalledWith(false);
    stream.emit("turn", { payload: mcq({ notices: ["Switched to a backup model while the main one is busy."] }) });
    expect(onBackup).toHaveBeenLastCalledWith(true);
  });

  it("sends a dispute, shows the re-check, then its result on the card", async () => {
    function Disputing() {
      const study = useStudy("s1");
      return <Thread study={study} onDispute={(id) => study.dispute(id, "correct", "TTL counts.")} />;
    }
    render(<Disputing />);
    const start = await lastStream();
    const graded = mcq({
      can_dispute: true,
      feedback: {
        value: "partial",
        explanation: "Who sets the lifetime is missing.",
        misconception: null,
        claims: [],
        citations: [],
        general_knowledge: false,
      },
    });
    start.emit("started", { thread_id: "t1", greeting: "Hello." });
    start.emit("turn", { payload: graded });
    await start.end();
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    const sent = await lastStream();
    const body = sent.body as { reply: { dispute: { id: string; reason: string; explanation: string } } };
    expect(body.reply.dispute).toMatchObject({ reason: "correct", explanation: "TTL counts." });
    expect(screen.getByText("Dispute sent. A second grader is re-checking your answer…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dispute grade" })).not.toBeInTheDocument();
    sent.emit("step", { label: "Re-checking…" });
    sent.emit("turn", {
      payload: {
        ...graded,
        can_dispute: false,
        dispute_result: {
          outcome: "changed",
          original_grade: "partial",
          new_grade: "correct",
          explanation: "",
          citations: [],
          concept: "Subnetting",
          stage: 3,
        },
      },
    });
    await sent.end();
    expect(screen.getByText(/Re-check: raised to Correct — Subnetting back to ●●●○○ level 3\./)).toBeInTheDocument();
    expect(screen.getAllByText("Multiple choice · Subnetting · Q1")).toHaveLength(1);
  });

  it("puts a refused dispute's card back as it was, with the error shown", async () => {
    function Disputing() {
      const study = useStudy("s1");
      return <Thread study={study} onDispute={(id) => study.dispute(id, "other", "")} />;
    }
    render(<Disputing />);
    const start = await lastStream();
    start.emit("started", { thread_id: "t1", greeting: "Hello." });
    start.emit("turn", {
      payload: mcq({
        can_dispute: true,
        feedback: {
          value: "partial",
          explanation: "Close.",
          misconception: null,
          claims: [],
          citations: [],
          general_knowledge: false,
        },
      }),
    });
    await start.end();
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    await (await lastStream()).fail(new ApiError(404, "Not found."));
    expect(screen.getByRole("alert")).toHaveTextContent("Not found.");
    expect(screen.queryByText(/Dispute sent/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dispute grade" })).toBeInTheDocument();
    const saved = JSON.parse(window.sessionStorage.getItem("ebb-thread:t1")!) as Card[];
    expect(saved.find((c) => c.kind === "grade")).toMatchObject({ dispute: null, canDispute: true });
  });

  it("offers Retry on a failed grade, sending the same answer to the question it brought back", async () => {
    await startedAt(short());
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    reply.emit("turn", { payload: short({ notices: [EXHAUSTED] }) });
    await reply.end();
    expect(screen.getByRole("alert")).toHaveTextContent(EXHAUSTED);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    const again = (await lastStream()).body as { reply: { attempt_id: string; answer: string } };
    expect(again.reply.answer).toBe("Speed.");
    expect(again.reply.attempt_id).not.toBe((reply.body as typeof again).reply.attempt_id);
  });

  it("shows the error card instead of crashing when the API can't be reached", async () => {
    vi.mocked(getJson).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<Harness />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the API.");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect((await lastStream()).path).toBe("/subjects/s1/session");
  });

  it("reconnects a thread saved in this tab without the Resume card", async () => {
    const saved: Card[] = [{ kind: "tutor", id: "c1", text: "Welcome back — 1 idea is ebbing. Reviewing those first." }];
    saveThread("t9", saved);
    rememberThread("s1", "t9");
    render(<Harness />);
    const stream = await lastStream();
    expect(stream.path).toBe("/sessions/t9/resume");
    expect(stream.body).toEqual({ driver: "drv-1" });
    expect(getJson).not.toHaveBeenCalled();
    expect(screen.getByText("Welcome back — 1 idea is ebbing. Reviewing those first.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    stream.emit("turn", { payload: mcq() });
    expect(screen.getByText("Multiple choice · Subnetting · Q1")).toBeInTheDocument();
  });

  it("offers to resume another tab's session, and resumes it", async () => {
    vi.mocked(getJson).mockResolvedValue({
      offer: { thread_id: "t5", concept: "DNS resolution", number: 2, last_active_at: new Date().toISOString(), busy: false },
    });
    render(<Harness />);
    await userEvent.click(await screen.findByRole("button", { name: "Resume" }));
    const stream = await lastStream();
    expect(stream.path).toBe("/sessions/t5/resume");
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("starts fresh by ending the old session first", async () => {
    vi.mocked(getJson).mockResolvedValue({
      offer: { thread_id: "t5", concept: "DNS resolution", number: 2, last_active_at: new Date().toISOString(), busy: true },
    });
    render(<Harness />);
    await userEvent.click(await screen.findByRole("button", { name: "Start fresh" }));
    expect(postJson).toHaveBeenCalledWith("/sessions/t5/discard", { driver: "drv-1" }, expect.anything());
    expect((await lastStream()).path).toBe("/subjects/s1/session");
  });

  it("ends the session with End session and shows the summary", async () => {
    await startedAt(short());
    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    const quit = await lastStream();
    expect(quit.body).toEqual({ driver: "drv-1", reply: { quit: true } });
    quit.emit("finished", finished);
    await quit.end();
    expect(screen.getByText("No answers this session. Your memory levels are unchanged.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End session" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toBeDisabled();
  });

  it("sends a short answer from the composer and grades it in the next turn", async () => {
    const onDispute = vi.fn();
    render(<Harness onDispute={onDispute} />);
    const start = await lastStream();
    start.emit("started", { thread_id: "t1", greeting: "Hello." });
    start.emit("turn", { payload: short() });
    await start.end();
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const card = screen.getByRole("article", { name: "Short-answer question" });
    expect(card).toHaveTextContent("Your answerSpeed.");
    const reply = await lastStream();
    reply.emit("turn", {
      payload: mcq({
        number: 1,
        can_dispute: true,
        feedback: {
          value: "partial",
          explanation: "Who sets the lifetime is missing.",
          misconception: null,
          claims: [],
          citations: [],
          general_knowledge: false,
        },
        notices: ["Switched to a backup model while the main one is busy."],
      }),
    });
    await reply.end();
    expect(screen.getByRole("article", { name: "Grade: Partial" })).toBeInTheDocument();
    expect(screen.getByText("Switched to a backup model while the main one is busy.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    expect(onDispute).toHaveBeenCalled();
  });
});

describe("this tab's own session", () => {
  const BUSY = "That session is open in another tab or terminal.";
  const FINISHING = /Finishing your last answer…/;
  const sessionLookups = () => vi.mocked(getJson).mock.calls.filter(([path]) => path.startsWith("/subjects/s1/session"));

  /** The tab was refreshed (or came back from Progress) while its answer was being graded. */
  function refreshedMidTurn() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const answered: Card[] = [
      { kind: "tutor", id: "c1", text: "Hello." },
      {
        kind: "question",
        id: "c2",
        concept: "Subnetting",
        number: 1,
        question: mcq().question,
        answer: { attempt_id: "a1", text: "62", choice: 1 },
        result: null,
      },
    ];
    saveThread("t9", answered);
    rememberThread("s1", "t9");
    render(<Harness />);
  }

  afterEach(() => vi.useRealTimers());

  it("waits for its own last turn to finish instead of showing it as busy", async () => {
    refreshedMidTurn();
    await (await lastStream()).fail(new ApiError(409, BUSY));
    expect(screen.getByText(FINISHING)).toBeInTheDocument();
    expect(screen.queryByText(BUSY)).not.toBeInTheDocument();
    expect(streams).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1_000));
    const again = await lastStream();
    expect(streams).toHaveLength(2);
    expect(again.body).toEqual({ driver: "drv-1" });
    expect(again.path).toBe("/sessions/t9/resume");
    expect(screen.getByText(FINISHING)).toBeInTheDocument();
    again.emit("turn", { payload: mcq({ number: 2, feedback: { value: "correct", explanation: "Yes.", misconception: null, claims: [], citations: [], general_knowledge: false } }) });
    await again.end();
    expect(screen.queryByText(FINISHING)).not.toBeInTheDocument();
    expect(screen.getByText("Multiple choice · Subnetting · Q2")).toBeInTheDocument();
    expect(sessionLookups()).toHaveLength(0);
  });

  it("reopens an answer the API never got, rather than showing its question twice", async () => {
    refreshedMidTurn();
    const stream = await lastStream();
    stream.emit("turn", { payload: mcq() }); // the reply was lost: the same question still waits
    await stream.end();
    expect(screen.getAllByText("Multiple choice · Subnetting · Q1")).toHaveLength(1);
    await userEvent.keyboard("2{Enter}");
    const sent = (await lastStream()).body as { reply: { answer: string } };
    expect(sent.reply.answer).toBe("2");
  });

  it("offers the session once it has waited about 30 seconds", async () => {
    refreshedMidTurn();
    let waited = 0;
    while (sessionLookups().length === 0 && waited < 60_000) {
      const last = streams[streams.length - 1];
      if (last) await last.fail(new ApiError(409, BUSY));
      await act(async () => vi.advanceTimersByTime(500));
      waited += 500;
    }
    expect(waited).toBeGreaterThanOrEqual(25_000);
    expect(waited).toBeLessThanOrEqual(40_000);
    expect(streams.length).toBeGreaterThan(3); // tried again and again, not every 500 ms
    expect(streams.length).toBeLessThan(20);
    expect(streams.slice(0, -1).every((s) => s.path === "/sessions/t9/resume")).toBe(true);
    expect(streams[streams.length - 1].path).toBe("/subjects/s1/session"); // nothing to offer: a new one
    expect(screen.queryByText(FINISHING)).not.toBeInTheDocument();
  });

  it("lets go of the session when the page is closed", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await startedAt(mcq());
    act(() => void window.dispatchEvent(new Event("pagehide")));
    expect(fetch).toHaveBeenCalledWith(`${API_URL}/sessions/t1/release?driver=drv-1`, {
      method: "POST",
      keepalive: true,
    });
    fetch.mockRestore();
  });

  it("has nothing to let go of before a session has started", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    render(<Harness />);
    await lastStream();
    act(() => void window.dispatchEvent(new Event("pagehide")));
    expect(fetch).not.toHaveBeenCalled();
    fetch.mockRestore();
  });
});

describe("the learn phase", () => {
  const learnTurn = (over: Partial<LearnTurn> = {}): LearnTurn => ({
    kind: "learn",
    concept: "TCP handshake",
    concept_id: "c1",
    number: 0,
    feedback: null,
    lesson: null,
    notices: [],
    can_dispute: false,
    dispute_result: null,
    ...over,
  });
  const notes = {
    kind: "chunk" as const,
    ref: "k1",
    label: "Week_1.pdf, p. 14",
    page: 14,
    quote: "The SYN carries the starting number.",
    name: "Week_1.pdf",
    heading: null,
    title: null,
  };
  const lesson: StoredLesson = {
    concept_id: "c1",
    markdown: "# TCP handshake\n\nThree messages open a connection.\n\n| Step | Flags |\n|---|---|\n| 1 | SYN |",
    citations: [notes],
    web_pages: [],
    suggestions: ["How do SYN cookies work?", "What does TLS add?"],
    checks: ["Why can't the server skip the SYN-ACK?", "What ack follows a SYN numbered 1000?"],
    source_summary: { notes: 2, notes_pages: [12, 15], web_pages: 2, label: null },
    model: "m",
    written_at: "2026-10-31T09:00:00Z",
    learned_at: null,
  };
  const payload = (over: Partial<LearnPayload> = {}): LearnPayload => ({
    kind: "learn",
    concept_id: "c1",
    concept: "TCP handshake",
    lesson,
    exchanges: [],
    suggestions: lesson.suggestions,
    notices: [],
    has_notes: true,
    ...over,
  });
  const exchange = (question: string, kind: Exchange["kind"]): Exchange => ({
    position: 0,
    kind,
    question,
    answer: "Because the SYN-ACK carries the server's own number.",
    citations: [],
    suggestions: ["What is a SYN flood?"],
    asked_at: "2026-10-31T09:01:00Z",
  });

  /** The quiz paused on a new concept; its lesson is being started. */
  async function learning(turn: LearnTurn = learnTurn()) {
    await startedAt(turn);
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/concepts/c1/learn"));
    return streams.at(-1)!;
  }

  async function lessonShown() {
    const start = await learning();
    start.emit("learn", { payload: payload() });
    await start.end();
  }

  it("LearnLesson streams deltas then shows chips", async () => {
    const start = await learning();
    expect(start.body).toEqual({});
    start.emit("step", { label: "Writing the lesson…" });
    expect(screen.getByText(/Writing the lesson…/)).toBeInTheDocument();
    start.emit("text", { delta: "# TCP handshake\n\nThree mess" });
    start.emit("text", { delta: "ages, streamed  " });
    const article = screen.getByRole("article", { name: "Lesson" });
    expect(within(article).getByText("Lesson · quiz opens when you press Ready")).toBeInTheDocument();
    expect(within(article).getByText(/Three messages, streamed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "I'm ready, quiz me" })).not.toBeInTheDocument();
    // The kept markdown replaces what streamed.
    start.emit("learn", { payload: payload() });
    await start.end();
    expect(screen.queryByText(/streamed/)).not.toBeInTheDocument();
    expect(within(article).getByText("Three messages open a connection.")).toBeInTheDocument();
    expect(within(article).getByRole("table")).toBeInTheDocument();
    expect(within(article).getByText("Your notes · pp. 12 to 15")).toBeInTheDocument();
    expect(within(article).getByText("2 web sources")).toBeInTheDocument();
    expect(within(article).getByRole("button", { name: "[Notes p.14]" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Answer this for me" })).toHaveLength(2);
    expect(screen.getByText("Suggested next")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "What does TLS add?" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "I'm ready, quiz me" })).toBeEnabled();
    // Kept in the tab, like the quiz's cards.
    const saved = JSON.parse(window.sessionStorage.getItem("ebb-thread:t1")!) as Card[];
    expect(saved.find((c) => c.kind === "learn")).toMatchObject({ conceptId: "c1", lesson: { markdown: lesson.markdown } });
  });

  it("discard clears text and shows Retry", async () => {
    await lessonShown();
    await userEvent.click(screen.getByRole("button", { name: "What does TLS add?" }));
    const ask = streams.at(-1)!;
    const sent = ask.body as { reply: { ask: string; kind: string; ask_id: string } };
    expect(sent.reply).toMatchObject({ ask: "What does TLS add?", kind: "suggested" });
    ask.emit("text", { delta: "TLS adds encryp" });
    expect(screen.getByText("TLS adds encryp")).toBeInTheDocument();
    ask.emit("discard", {});
    expect(screen.queryByText("TLS adds encryp")).not.toBeInTheDocument();
    ask.emit("error", { notice: EXHAUSTED });
    await ask.end();
    expect(screen.getByRole("alert")).toHaveTextContent(EXHAUSTED);
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toHaveFocus();
    expect(screen.getByText("What does TLS add?", { selector: "p" })).toBeInTheDocument(); // the question stays
    await userEvent.click(retry);
    const again = streams.at(-1)!;
    expect(again).not.toBe(ask);
    expect(again.path).toBe("/concepts/c1/learn/reply");
    expect(again.body).toEqual(sent); // the same ask_id: the server retries that question
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Answer this for me posts a check ask", async () => {
    await lessonShown();
    const check = lesson.checks[0];
    await userEvent.click(screen.getAllByRole("button", { name: "Answer this for me" })[0]);
    const ask = streams.at(-1)!;
    expect(ask.path).toBe("/concepts/c1/learn/reply");
    const sent = ask.body as { reply: { ask: string; kind: string; ask_id: string } };
    expect(sent.reply.ask).toBe(check);
    expect(sent.reply.kind).toBe("check");
    expect(sent.reply.ask_id).toMatch(/^[0-9a-f-]{36}$/);
    // While it is answered, nothing else can be asked.
    expect(screen.getByRole("button", { name: "Answer this for me" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "I'm ready, quiz me" })).toBeDisabled();
    expect(screen.getByPlaceholderText("Ask your own question about TCP handshake")).toBeDisabled();
    ask.emit("text", { delta: "Because the SYN-ACK" });
    ask.emit("learn", {
      payload: payload({ exchanges: [exchange(check, "check")], suggestions: ["What is a SYN flood?"] }),
    });
    await ask.end();
    expect(screen.getByText("Because the SYN-ACK carries the server's own number.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Answer this for me" })).toHaveLength(1); // asked: no button
    expect(screen.getByRole("button", { name: "What is a SYN flood?" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "What does TLS add?" })).not.toBeInTheDocument();
  });

  it("composer placeholder names the concept during learn", async () => {
    await lessonShown();
    const box = screen.getByPlaceholderText("Ask your own question about TCP handshake");
    expect(box).toBeEnabled();
    await userEvent.type(box, "Is UDP the same?{Enter}");
    const sent = streams.at(-1)!.body as { reply: { ask: string; kind: string } };
    expect(sent.reply).toMatchObject({ ask: "Is UDP the same?", kind: "typed" });
    expect(screen.getByText("Is UDP the same?", { selector: "p" })).toBeInTheDocument();
  });

  it("Ready posts ready then learned to the quiz", async () => {
    await lessonShown();
    await userEvent.click(screen.getByRole("button", { name: "I'm ready, quiz me" }));
    const ready = streams.at(-1)!;
    expect(ready.path).toBe("/concepts/c1/learn/reply");
    expect(ready.body).toEqual({ reply: { ready: true } });
    ready.emit("learned", { concept_id: "c1" });
    await ready.end();
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/sessions/t1/reply"));
    const quiz = streams.at(-1)!;
    expect(quiz.body).toEqual({ driver: "drv-1", reply: { learned: true } });
    quiz.emit("turn", { payload: mcq({ concept: "TCP handshake" }) });
    await quiz.end();
    expect(screen.getByText("Multiple choice · TCP handshake · Q1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "I'm ready, quiz me" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Answer this for me" })).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Lesson" })).toHaveTextContent("Three messages open a connection.");
    expect(screen.getByRole("radio", { name: "64" })).toHaveFocus();
    expect(screen.getByPlaceholderText("Type your answer…")).toBeDisabled();
  });

  it("shows the last answer's grade above the lesson, and starts the lesson again on a refresh", async () => {
    const graded = learnTurn({
      feedback: { value: "correct", explanation: "Yes.", misconception: null, claims: [], citations: [], general_knowledge: false },
      can_dispute: true,
    });
    const start = await learning(graded);
    start.emit("learn", { payload: payload() });
    await start.end();
    const order = screen.getAllByRole("article").map((a) => a.getAttribute("aria-label"));
    expect(order).toEqual(["Grade: Correct", "Lesson"]);
    cleanup();
    streams = [];
    // The same tab, refreshed: the saved cards show at once, then the session and the lesson reconnect.
    render(<Harness />);
    const resume = await lastStream();
    expect(resume.path).toBe("/sessions/t1/resume");
    expect(screen.getByText("Three messages open a connection.")).toBeInTheDocument();
    resume.emit("turn", { payload: graded });
    await resume.end();
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/concepts/c1/learn"));
    const again = streams.at(-1)!;
    again.emit("learn", { payload: payload() });
    await again.end();
    expect(screen.getAllByRole("article", { name: "Lesson" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "I'm ready, quiz me" })).toHaveLength(1);
  });

  it("hands over to the quiz after a refresh lost the learned reply, and Retry sends it again", async () => {
    // Ready answered `learned`, then the tab went away before the quiz heard of it.
    const saved: Card[] = [
      { kind: "tutor", id: "g", text: "Hello." },
      { kind: "learn", id: "l1", conceptId: "c1", concept: "TCP handshake", lesson, hasNotes: true, learned: true },
    ];
    saveThread("t1", saved);
    rememberThread("s1", "t1");
    render(<Harness />);
    const resume = await lastStream();
    expect(resume.path).toBe("/sessions/t1/resume");
    resume.emit("turn", { payload: learnTurn() }); // the session still waits on the lesson
    await resume.end();
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/sessions/t1/reply"));
    const handOver = streams.at(-1)!;
    expect(handOver.body).toEqual({ driver: "drv-1", reply: { learned: true } });
    expect(streams.filter((s) => s.path.startsWith("/concepts/"))).toHaveLength(0);
    await handOver.fail(new ApiError(200, CUT_OFF));
    expect(screen.getByRole("alert")).toHaveTextContent(CUT_OFF);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    const again = streams.at(-1)!;
    expect(again).not.toBe(handOver);
    expect(again.body).toEqual(handOver.body);
    again.emit("turn", { payload: mcq({ concept: "TCP handshake" }) });
    await again.end();
    expect(screen.getByText("Multiple choice · TCP handshake · Q1")).toBeInTheDocument();
    expect(streams.filter((s) => s.body.reply && "learned" in (s.body.reply as object))).toHaveLength(2);
  });

  it("still hands over when the Ready call is cut off after learned", async () => {
    await lessonShown();
    await userEvent.click(screen.getByRole("button", { name: "I'm ready, quiz me" }));
    const ready = streams.at(-1)!;
    ready.emit("learned", { concept_id: "c1" });
    await ready.fail(new ApiError(200, CUT_OFF));
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/sessions/t1/reply"));
    expect(streams.at(-1)!.body).toEqual({ driver: "drv-1", reply: { learned: true } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts the lesson again when a dispute follows a failed start", async () => {
    function Disputing() {
      const study = useStudy("s1");
      return <Thread study={study} onDispute={(id) => study.dispute(id, "correct", "")} />;
    }
    const graded = learnTurn({
      feedback: { value: "partial", explanation: "Close.", misconception: null, claims: [], citations: [], general_knowledge: false },
      can_dispute: true,
    });
    render(<Disputing />);
    const quiz = await lastStream();
    quiz.emit("started", { thread_id: "t1", greeting: "Hello." });
    quiz.emit("turn", { payload: graded });
    await quiz.end();
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/concepts/c1/learn"));
    const start = streams.at(-1)!;
    start.emit("error", { notice: EXHAUSTED });
    await start.end();
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    const dispute = streams.at(-1)!;
    expect(dispute.path).toBe("/sessions/t1/reply");
    dispute.emit("turn", { payload: { ...graded, can_dispute: false, dispute_result: { outcome: "refused" } } });
    await dispute.end();
    await waitFor(() => expect(streams.at(-1)!.path).toBe("/concepts/c1/learn"));
    expect(streams.at(-1)).not.toBe(start);
  });

  it("marks an answer with no notes citation from the subject's has_notes", async () => {
    const start = await learning();
    const webOnly = { ...lesson, citations: [], source_summary: { notes: 0, notes_pages: [], web_pages: 1, label: null } };
    start.emit("learn", { payload: payload({ lesson: webOnly, has_notes: true }) });
    await start.end();
    expect(within(screen.getByRole("article", { name: "Lesson" })).getByText("Not in your notes")).toBeInTheDocument();
  });
});
