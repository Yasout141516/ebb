import type { DisputeResult, Finished, Grade, GradeValue, Lesson, Question, TurnPayload } from "./types";

// A study thread: the tab's cards for one session, kept in sessionStorage so a refresh (or
// Study → Progress → Study) reconnects without the Resume card.

/** What the learner sent for a question: the option's position for multiple choice. */
export type Answer = { attempt_id: string; text: string; choice: number | null };

export type QuestionCard = {
  kind: "question";
  id: string;
  concept: string;
  number: number;
  question: Question;
  answer: Answer | null;
  result: GradeValue | null; // the grade its answer got, once known
};

export type GradeCard = {
  kind: "grade";
  id: string;
  grade: Grade;
  canDispute: boolean;
  dispute: "checking" | DisputeResult | null;
};

export type Card =
  | { kind: "day"; id: string; text: string }
  | { kind: "tutor"; id: string; text: string }
  | { kind: "user"; id: string; text: string }
  | GradeCard
  | { kind: "lesson"; id: string; lesson: Lesson }
  | { kind: "notice"; id: string; text: string }
  | QuestionCard
  | { kind: "summary"; id: string; finished: Finished };

const DRIVER_KEY = "ebb-driver";
const threadKey = (threadId: string) => `ebb-thread:${threadId}`;
const activeKey = (subjectId: string) => `ebb-active:${subjectId}`;

/** A random UUID, also where crypto.randomUUID is missing (a page not served securely). */
export function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function read(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // storage blocked or full: this tab just won't reconnect silently
  }
}

let fallbackDriver: string | null = null;

/** This tab's driver id: one UUID per tab, kept across refreshes. */
export function driverId(): string {
  const saved = read(DRIVER_KEY);
  if (saved) return saved;
  const id = fallbackDriver ?? uuid();
  write(DRIVER_KEY, id);
  fallbackDriver = id; // storage blocked: still one id for this page's life
  return id;
}

export function saveThread(threadId: string, cards: Card[]): void {
  write(threadKey(threadId), JSON.stringify(cards));
}

export function loadThread(threadId: string): Card[] | null {
  const text = read(threadKey(threadId));
  if (text === null) return null;
  try {
    const cards: unknown = JSON.parse(text);
    return Array.isArray(cards) ? (cards as Card[]) : null;
  } catch {
    return null;
  }
}

/** The open session this tab has for the subject, if any. */
export function activeThread(subjectId: string): string | null {
  return read(activeKey(subjectId));
}

export function rememberThread(subjectId: string, threadId: string): void {
  write(activeKey(subjectId), threadId);
}

/** The subject's session ended (or can't be reconnected): forget it and its cards. */
export function forgetThread(subjectId: string): void {
  const threadId = activeThread(subjectId);
  if (threadId) write(threadKey(threadId), null);
  write(activeKey(subjectId), null);
}

// The thread's cards, changed only through these.

/** The question waiting for an answer, if one is. */
export function waitingQuestion(cards: Card[]): QuestionCard | null {
  const last = cards.findLast((c) => c.kind === "question" || c.kind === "summary");
  return last?.kind === "question" && last.answer === null ? last : null;
}

const closeDisputes = (cards: Card[]): Card[] =>
  cards.map((c) => (c.kind === "grade" && c.canDispute ? { ...c, canDispute: false } : c));

const sameQuestion = (card: QuestionCard, payload: TurnPayload) =>
  card.concept === payload.concept &&
  card.number === payload.number &&
  card.question.prompt === payload.question.prompt;

function graded(cards: Card[], grade: Grade | null): Card[] {
  if (!grade) return cards;
  const last = cards.findLast((c) => c.kind === "question" && c.answer !== null);
  return cards.map((c) => (c === last ? { ...c, result: grade.value } : c));
}

function feedbackCards(grade: Grade | null, lesson: Lesson | null, notices: string[], canDispute: boolean): Card[] {
  const cards: Card[] = [];
  if (grade) cards.push({ kind: "grade", id: uuid(), grade, canDispute, dispute: null });
  if (lesson) cards.push({ kind: "lesson", id: uuid(), lesson });
  for (const text of notices) cards.push({ kind: "notice", id: uuid(), text });
  return cards;
}

const questionCard = (payload: TurnPayload): QuestionCard => ({
  kind: "question",
  id: uuid(),
  concept: payload.concept,
  number: payload.number,
  question: payload.question,
  answer: null,
  result: null,
});

/** A turn's cards, in the spec's order: grade, lesson, notices, question. */
export function applyTurn(cards: Card[], payload: TurnPayload): Card[] {
  const checking = cards.findLast((c) => c.kind === "grade" && c.dispute === "checking");
  if (payload.dispute_result && checking) {
    // The re-check's result goes on the disputed card; the question is shown again.
    const waiting = waitingQuestion(cards);
    const kept = cards
      .filter((c) => c !== waiting)
      .map((c) => (c === checking ? { ...c, dispute: payload.dispute_result } : c));
    return [...kept, ...feedbackCards(null, null, payload.notices, false), questionCard(payload)];
  }
  const waiting = waitingQuestion(cards);
  if (waiting && sameQuestion(waiting, payload)) return cards; // a reconnect: already shown
  const before = closeDisputes(graded(cards, payload.feedback));
  const added = feedbackCards(payload.feedback, payload.lesson, payload.notices, payload.can_dispute);
  if (payload.dispute_result) {
    // Resumed in a new tab: the re-checked grade has no card yet.
    for (const c of added) if (c.kind === "grade") c.dispute = payload.dispute_result;
  }
  return [...before, ...added, questionCard(payload)];
}

/** The learner answered the waiting question: it locks, and the answer is their bubble. */
export function answered(cards: Card[], answer: Answer): Card[] {
  const waiting = waitingQuestion(cards);
  const marked = closeDisputes(cards).map((c) => (c === waiting ? { ...c, answer } : c));
  return [...marked, { kind: "user", id: uuid(), text: answer.text }];
}

/** A dispute was sent for a grade card: it shows the re-check running. */
export function disputeSent(cards: Card[], gradeId: string): Card[] {
  return cards.map((c) =>
    c.kind === "grade" && c.id === gradeId ? { ...c, canDispute: false, dispute: "checking" } : c,
  );
}

/** The session ended: the last grade, lesson and notices, then the summary. */
export function applyFinished(cards: Card[], finished: Finished): Card[] {
  const { grade, lesson, notices } = finished.final;
  return [
    ...closeDisputes(graded(cards, grade)),
    ...feedbackCards(grade, lesson, notices, false),
    { kind: "summary", id: uuid(), finished },
  ];
}
