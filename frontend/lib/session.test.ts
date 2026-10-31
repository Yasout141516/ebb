import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activeThread,
  answered,
  applyFinished,
  applyLearn,
  applyTurn,
  asked,
  type Card,
  disputeSent,
  type ExchangeCard,
  learned,
  learningCard,
  driverId,
  forgetThread,
  loadThread,
  rememberThread,
  restored,
  saveThread,
  waitingQuestion,
} from "./session";
import type { Exchange, Finished, Grade, LearnPayload, LearnTurn, StoredLesson, TurnPayload } from "./types";

const grade = (value: Grade["value"] = "partial"): Grade => ({
  value,
  explanation: "Close.",
  misconception: null,
  claims: [],
  citations: [],
  general_knowledge: false,
});

const turn = (over: Partial<TurnPayload> = {}): TurnPayload => ({
  concept: "Subnetting",
  number: 2,
  question: {
    type: "mcq",
    difficulty: "medium",
    prompt: "How many usable hosts in a /26?",
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

const kinds = (cards: Card[]) => cards.map((c) => c.kind);

beforeEach(() => window.sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("storage", () => {
  it("keeps one driver id per tab", () => {
    const first = driverId();
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(driverId()).toBe(first);
    expect(window.sessionStorage.getItem("ebb-driver")).toBe(first);
  });

  it("still gives a stable driver id when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const first = driverId();
    expect(driverId()).toBe(first);
  });

  it("saves and loads a thread, and names the subject's active one", () => {
    const cards: Card[] = [{ kind: "tutor", id: "a", text: "Welcome back." }];
    saveThread("t1", cards);
    expect(loadThread("t1")).toEqual(cards);
    expect(loadThread("t2")).toBeNull();
    expect(activeThread("s1")).toBeNull();
    rememberThread("s1", "t1");
    expect(activeThread("s1")).toBe("t1");
    forgetThread("s1");
    expect(activeThread("s1")).toBeNull();
  });

  it("treats unreadable storage as nothing saved", () => {
    window.sessionStorage.setItem("ebb-thread:t1", "{not json");
    expect(loadThread("t1")).toBeNull();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadThread("t1")).toBeNull();
    expect(activeThread("s1")).toBeNull();
    expect(() => saveThread("t1", [])).not.toThrow();
  });
});

describe("the thread", () => {
  it("appends a turn in order: grade, lesson, notices, question", () => {
    const cards = applyTurn([], turn({
      feedback: grade("incorrect"),
      lesson: { text: "A /26 leaves 6 host bits.", citations: [] },
      notices: ["Switched to a backup model while the main one is busy."],
      can_dispute: true,
    }));
    expect(kinds(cards)).toEqual(["grade", "lesson", "notice", "question"]);
    expect(cards[0]).toMatchObject({ kind: "grade", canDispute: true, dispute: null });
  });

  it("marks the answered question with its grade and closes older disputes", () => {
    let cards = applyTurn([], turn({ feedback: grade(), can_dispute: true }));
    const q = waitingQuestion(cards)!;
    cards = answered(cards, { attempt_id: "a1", text: "62", choice: 1 });
    expect(waitingQuestion(cards)).toBeNull();
    expect(cards.at(-1)).toMatchObject({ kind: "question", answer: { text: "62", choice: 1 } });
    expect(cards).toHaveLength(2); // no separate bubble
    expect(cards.find((c) => c.kind === "grade")).toMatchObject({ canDispute: false });
    cards = applyTurn(cards, turn({ number: 3, feedback: grade("correct") }));
    expect(cards.find((c) => c.id === q.id)).toMatchObject({ result: "correct" });
  });

  it("puts cards back as they were when a reply is refused", () => {
    const before = applyTurn([], turn({ feedback: grade(), can_dispute: true }));
    const after = answered(before, { attempt_id: "a1", text: "62", choice: 1 });
    expect(restored(after, before)).toEqual(before);
  });

  it("ignores the waiting question coming back on a reconnect", () => {
    const cards = applyTurn([], turn());
    expect(applyTurn(cards, turn())).toBe(cards);
  });

  it("reopens an answer the API never got when a reconnect brings its question back", () => {
    // The reply was lost on the way, then the tab was refreshed: the same turn comes back.
    const shown = applyTurn([], turn({ feedback: grade("correct") }));
    const sent = answered(shown, { attempt_id: "a1", text: "62", choice: 1 });
    const back = applyTurn(sent, turn({ feedback: grade("correct") }), true);
    expect(kinds(back)).toEqual(kinds(shown)); // the earlier grade isn't shown twice
    expect(waitingQuestion(back)).toMatchObject({ id: waitingQuestion(shown)!.id, answer: null });
  });

  it("keeps the answer and adds the question again when a reply brings it back", () => {
    // e.g. its grade failed: the notice and the question come back for a retry.
    const sent = answered(applyTurn([], turn()), { attempt_id: "a1", text: "62", choice: 1 });
    const back = applyTurn(sent, turn({ notices: ["The tutor couldn't respond."] }));
    expect(kinds(back)).toEqual(["question", "notice", "question"]);
    expect(back[0]).toMatchObject({ answer: { attempt_id: "a1" } });
  });

  it("puts a re-check's result on the disputed card and shows the question again", () => {
    let cards = applyTurn([], turn({ feedback: grade(), can_dispute: true }));
    const gradeCard = cards[0];
    cards = disputeSent(cards, gradeCard.id);
    expect(cards[0]).toMatchObject({ dispute: "checking", canDispute: false });
    const result = {
      outcome: "upheld" as const,
      original_grade: "partial" as const,
      new_grade: null,
      explanation: "Your notes say otherwise.",
      citations: [],
      concept: "Subnetting",
      stage: 1,
    };
    cards = applyTurn(cards, turn({ dispute_result: result, notices: ["n"] }));
    expect(kinds(cards)).toEqual(["grade", "notice", "question"]);
    expect(cards[0]).toMatchObject({ dispute: result });
  });

  it("ends with the last grade, lesson, notices and the summary", () => {
    const finished: Finished = {
      results: [],
      final: { grade: grade("correct"), lesson: null, notices: ["n"] },
      caught_up: false,
      next_review_at: null,
      next_review_line: null,
    };
    let cards = applyTurn([], turn({ feedback: grade(), can_dispute: true }));
    cards = answered(cards, { attempt_id: "a1", text: "62", choice: 1 });
    cards = applyFinished(cards, finished);
    expect(kinds(cards)).toEqual(["grade", "question", "grade", "notice", "summary"]);
    expect(cards.filter((c) => c.kind === "grade").every((c) => !c.canDispute)).toBe(true);
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
  const lesson: StoredLesson = {
    concept_id: "c1",
    markdown: "# TCP handshake\n\nThree messages.",
    citations: [],
    web_pages: [],
    suggestions: ["What is a SYN flood?"],
    checks: ["Why three and not four?"],
    source_summary: { notes: 1, notes_pages: [14], web_pages: 0, label: "Your notes only" },
    model: "m",
    written_at: "2026-10-31T09:00:00Z",
    learned_at: null,
  };
  const exchange = (position: number, question: string): Exchange => ({
    position,
    kind: "typed",
    question,
    answer: `About ${question}`,
    citations: [],
    suggestions: [`After ${question}`],
    asked_at: "2026-10-31T09:01:00Z",
  });
  const payload = (over: Partial<LearnPayload> = {}): LearnPayload => ({
    kind: "learn",
    concept_id: "c1",
    concept: "TCP handshake",
    lesson,
    exchanges: [],
    suggestions: lesson.suggestions,
    notices: [],
    ...over,
  });
  const ask = (question: string): ExchangeCard => ({
    kind: "exchange",
    id: `ask-${question}`,
    conceptId: "c1",
    question,
    askKind: "typed",
    exchange: null,
  });

  it("puts the last answer's grade first, then the new concept's lesson", () => {
    let cards = answered(applyTurn([], turn()), { attempt_id: "a1", text: "62", choice: 1 });
    cards = applyTurn(cards, learnTurn({ feedback: grade("correct"), can_dispute: true }));
    expect(kinds(cards)).toEqual(["question", "grade", "learn"]);
    expect(cards[0]).toMatchObject({ result: "correct" });
    expect(learningCard(cards)).toMatchObject({ conceptId: "c1", concept: "TCP handshake", lesson: null });
    expect(waitingQuestion(cards)).toBeNull();
    // A reconnect brings the same learn turn back: nothing changes.
    expect(applyTurn(cards, learnTurn({ feedback: grade("correct") }), true)).toBe(cards);
  });

  it("keeps the lesson in place when a dispute's result comes back on the learn turn", () => {
    let cards = applyTurn([], learnTurn({ feedback: grade(), can_dispute: true }));
    cards = applyLearn(cards, payload());
    cards = disputeSent(cards, cards[0].id);
    const result = { outcome: "refused" as const };
    cards = applyTurn(cards, learnTurn({ feedback: grade(), dispute_result: result }));
    expect(kinds(cards)).toEqual(["grade", "learn", "ready"]);
    expect(cards[0]).toMatchObject({ dispute: result });
  });

  it("fills the lesson, then answers the waiting question and keeps Ready last", () => {
    let cards = applyTurn([], learnTurn());
    cards = applyLearn(cards, payload({ notices: ["Web search unavailable — this lesson uses your notes only."] }));
    expect(kinds(cards)).toEqual(["learn", "notice", "ready"]);
    expect(cards[0]).toMatchObject({ lesson });
    cards = asked(cards, ask("Why SYN?"));
    expect(kinds(cards)).toEqual(["learn", "notice", "exchange", "ready"]);
    const next = payload({ exchanges: [exchange(0, "Why SYN?")], suggestions: ["After Why SYN?"] });
    cards = applyLearn(cards, { ...next, notices: ["Web search unavailable — this lesson uses your notes only."] });
    expect(kinds(cards)).toEqual(["learn", "notice", "exchange", "ready"]); // the notice isn't repeated
    expect(cards[2]).toMatchObject({ id: "ask-Why SYN?", exchange: { answer: "About Why SYN?" } });
    expect(cards[3]).toMatchObject({ kind: "ready", suggestions: ["After Why SYN?"] });
  });

  it("drops a waiting question the server answered none for", () => {
    let cards = applyLearn(applyTurn([], learnTurn()), payload());
    cards = asked(cards, ask("Lost one"));
    cards = asked(cards, ask("Kept one"));
    cards = applyLearn(cards, payload({ exchanges: [exchange(0, "Kept one")] }));
    expect(cards.filter((c) => c.kind === "exchange").map((c) => c.question)).toEqual(["Kept one"]);
  });

  it("shows the questions kept on the server when the tab has none (resumed elsewhere)", () => {
    const cards = applyLearn(applyTurn([], learnTurn()), payload({ exchanges: [exchange(0, "A"), exchange(1, "B")] }));
    expect(kinds(cards)).toEqual(["learn", "exchange", "exchange", "ready"]);
  });

  it("once learned, keeps the lesson and drops Ready and unanswered questions", () => {
    let cards = applyLearn(applyTurn([], learnTurn()), payload());
    cards = asked(cards, ask("Never answered"));
    cards = learned(cards, "c1");
    expect(kinds(cards)).toEqual(["learn"]);
    expect(cards[0]).toMatchObject({ learned: true });
    expect(learningCard(cards)).toBeNull();
  });
});
