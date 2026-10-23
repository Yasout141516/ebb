import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJson, postJson } from "@/lib/api";
import { saveThread, rememberThread, type Card } from "@/lib/session";
import { CUT_OFF, streamPost, type SseEvent } from "@/lib/sse";
import type { Finished, TurnPayload } from "@/lib/types";
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
  vi.mocked(getJson).mockResolvedValue({ offer: null });
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

async function startedAt(turn: TurnPayload) {
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

  it("sends an MCQ answer as a bubble, and Retry re-sends the same attempt", async () => {
    await startedAt(mcq());
    await userEvent.keyboard("2{Enter}");
    const reply = await lastStream();
    expect(reply.path).toBe("/sessions/t1/reply");
    const sent = reply.body as { driver: string; reply: { attempt_id: string; answer: string } };
    expect(sent.driver).toBe("drv-1");
    expect(sent.reply.answer).toBe("2");
    expect(sent.reply.attempt_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.getByText("62", { selector: "div" })).toBeInTheDocument();

    await reply.fail(new ApiError(200, CUT_OFF));
    expect(screen.getByRole("alert")).toHaveTextContent(CUT_OFF);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    const again = await lastStream();
    expect(streams).toHaveLength(3);
    expect(again.body).toEqual(sent);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a tutor failure in the stream as the error card with Retry", async () => {
    await startedAt(short());
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    reply.emit("error", { notice: EXHAUSTED });
    await reply.end();
    expect(screen.getByRole("alert")).toHaveTextContent(EXHAUSTED);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect((await lastStream()).body).toEqual(reply.body);
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
    expect(postJson).toHaveBeenCalledWith("/sessions/t5/discard", undefined, expect.anything());
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
    expect(screen.getByText("Speed.")).toBeInTheDocument();
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
