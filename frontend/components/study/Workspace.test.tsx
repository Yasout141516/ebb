import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJson, postJson } from "@/lib/api";
import { streamPost, type SseEvent } from "@/lib/sse";
import type { Concepts, Grade, TurnPayload } from "@/lib/types";
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
};
let streams: Stream[];

const memory: Concepts = {
  concepts: [
    {
      id: "c1",
      name: "DNS resolution",
      description: "",
      stage: 2,
      tide: 0.52,
      band: "low tide",
      next_review_at: new Date(Date.now() - 3_600_000).toISOString(),
      due: true,
      projected_tide: 0.38,
      learned: false,
    },
  ],
  due_count: 1,
  next_review_at: new Date(Date.now() - 3_600_000).toISOString(),
};

const short = (over: Partial<TurnPayload> = {}): TurnPayload => ({
  concept: "DNS resolution",
  number: 1,
  question: {
    type: "short",
    difficulty: "medium",
    prompt: "Why do resolvers cache answers?",
    options: [],
    source: "notes",
    source_line: null,
  },
  feedback: null,
  lesson: null,
  notices: [],
  can_dispute: false,
  dispute_result: null,
  ...over,
});

const partial: Grade = {
  value: "partial",
  explanation: "Who sets the lifetime is missing.",
  misconception: null,
  claims: [],
  citations: [],
  general_knowledge: false,
};

const conceptCalls = () => vi.mocked(getJson).mock.calls.filter(([path]) => path === "/subjects/s1/concepts").length;

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("ebb-driver", "drv-1");
  streams = [];
  vi.mocked(getJson).mockImplementation(async (path: string) => {
    if (path.startsWith("/subjects/s1/session")) return { offer: null };
    if (path === "/subjects") return [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 1 }];
    if (path === "/subjects/s1/sources") return [];
    if (path === "/subjects/s1/concepts") return memory;
    throw new Error(`unexpected ${path}`);
  });
  vi.mocked(postJson).mockResolvedValue(undefined);
  vi.mocked(streamPost).mockImplementation(
    (path, body, onEvent: (e: SseEvent) => void) =>
      new Promise<void>((resolve) => {
        streams.push({
          path,
          body: body as Record<string, unknown>,
          emit: (event, data) => act(() => onEvent({ event, data })),
          end: () => act(async () => resolve()),
        });
      }),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

const lastStream = async () => {
  await waitFor(() => expect(streams.length).toBeGreaterThan(0));
  return streams[streams.length - 1];
};

/** A started session whose short question was answered and graded Partial. */
async function gradedSession(canDispute: boolean) {
  render(<Workspace subjectId="s1" onBackup={() => {}} />);
  const start = await lastStream();
  start.emit("started", { thread_id: "t1", greeting: "Hello." });
  start.emit("turn", { payload: short() });
  await start.end();
  await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
  const reply = await lastStream();
  reply.emit("turn", { payload: short({ number: 2, feedback: partial, can_dispute: canDispute }) });
  await reply.end();
}

describe("Workspace", () => {
  it("lays out subjects and sources, the thread, and the memory panel", async () => {
    render(<Workspace subjectId="s1" onBackup={() => {}} />);
    const left = screen.getByRole("complementary", { name: "Subjects and sources" });
    expect(await within(left).findByText("8 concepts · 1 due today")).toBeInTheDocument();
    expect(await within(left).findByText("No sources yet")).toBeInTheDocument();
    const right = screen.getByRole("complementary", { name: "Memory" });
    expect(await within(right).findByText("Due for review · 1")).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "Study thread" })).toBeInTheDocument();
  });

  it("renders no Flashcards, Exam, Suggested reading, Review or Quiz ahead", async () => {
    await gradedSession(true);
    await screen.findByText("Due for review · 1", { selector: "aside *" });
    for (const text of [/Flashcards/i, /\bExam\b/i, /Suggested reading/i, /^Review$/, /Quiz ahead/i, /Review weak cards/i]) {
      expect(screen.queryByText(text)).toBeNull();
      expect(screen.queryByRole("button", { name: text })).toBeNull();
      expect(screen.queryByRole("link", { name: text })).toBeNull();
    }
  });

  it("refreshes the memory panel after every turn", async () => {
    render(<Workspace subjectId="s1" onBackup={() => {}} />);
    await waitFor(() => expect(conceptCalls()).toBe(1));
    const start = await lastStream();
    start.emit("started", { thread_id: "t1", greeting: "Hello." });
    start.emit("turn", { payload: short() });
    await start.end();
    await waitFor(() => expect(conceptCalls()).toBe(2));
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    reply.emit("turn", { payload: short({ number: 2, feedback: partial }) });
    await reply.end();
    await waitFor(() => expect(conceptCalls()).toBe(3));
  });

  it("has no Dispute grade when the grade can't be disputed", async () => {
    await gradedSession(false);
    expect(screen.getByRole("article", { name: "Grade: Partial" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dispute grade" })).toBeNull();
  });

  it("disputes a grade from the dialog and shows the re-check running", async () => {
    await gradedSession(true);
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    const dialog = screen.getByRole("dialog", { name: "Dispute this grade" });
    expect(
      within(dialog).getByText(
        "Short answer on DNS resolution, graded Partial. A second grader will re-check it.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByLabelText("The question is ambiguous"));
    await userEvent.type(within(dialog).getByLabelText("Explain (optional)"), "It could mean either.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send for re-check" }));

    const sent = await lastStream();
    expect(sent.path).toBe("/sessions/t1/reply");
    expect(sent.body).toEqual({
      driver: "drv-1",
      reply: { dispute: { id: expect.stringMatching(/^[0-9a-f-]{36}$/), reason: "ambiguous", explanation: "It could mean either." } },
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Dispute sent. A second grader is re-checking your answer…")).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Dispute grade" })).toBeNull();
  });

  it("Cancel sends nothing", async () => {
    await gradedSession(true);
    const before = streams.length;
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(streams).toHaveLength(before);
    expect(screen.getByRole("button", { name: "Dispute grade" })).toHaveFocus();
  });
});

describe("heartbeat", () => {
  const beats = () => vi.mocked(postJson).mock.calls.filter(([path]) => path === "/sessions/t1/heartbeat");

  async function openSession() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Workspace subjectId="s1" onBackup={() => {}} />);
    const start = await lastStream();
    start.emit("started", { thread_id: "t1", greeting: "Hello." });
    start.emit("turn", { payload: short() });
    await start.end();
  }

  it("keeps the session's lease every 30 seconds while a question waits", async () => {
    await openSession();
    expect(beats()).toHaveLength(0);
    await act(async () => vi.advanceTimersByTime(29_000));
    expect(beats()).toHaveLength(0);
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(beats()).toEqual([["/sessions/t1/heartbeat", { driver: "drv-1" }, expect.any(AbortSignal)]]);
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(beats()).toHaveLength(2);
  });

  it("ignores a session that has ended (404)", async () => {
    await openSession();
    vi.mocked(postJson).mockRejectedValue(new ApiError(404, "Not found."));
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(beats()).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Your answer")).toBeEnabled();
  });

  it("shows the busy card when another tab took the session over (409)", async () => {
    await openSession();
    vi.mocked(postJson).mockRejectedValue(new ApiError(409, "That session is open in another tab or terminal."));
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(screen.getByText("That session is open in another tab or terminal.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start fresh" })).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(beats()).toHaveLength(1);
  });

  it("skips the beat while a reply is in flight (the reply holds the lease)", async () => {
    await openSession();
    await userEvent.type(screen.getByLabelText("Your answer"), "Speed.{Enter}");
    const reply = await lastStream();
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(beats()).toHaveLength(0);
    reply.emit("turn", { payload: short({ number: 2, feedback: partial }) });
    await reply.end();
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(beats()).toHaveLength(1);
  });

  it("never overlaps beats, and abandons the one waiting when the page goes", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(postJson).mockImplementation((_path, _body, s) => {
      signal = s;
      return new Promise(() => {}); // the API never answers
    });
    await openSession();
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(beats()).toHaveLength(1);
    cleanup();
    expect(signal?.aborted).toBe(true);
  });

  it("stops once the session has ended", async () => {
    await openSession();
    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    const quit = await lastStream();
    quit.emit("finished", {
      results: [],
      final: { grade: null, lesson: null, notices: [] },
      caught_up: false,
      next_review_at: null,
      next_review_line: null,
    });
    await quit.end();
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(beats()).toHaveLength(0);
  });
});
