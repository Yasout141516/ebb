import type {
  AskKind,
  DisputeResult,
  Exchange,
  Finished,
  Grade,
  GradeValue,
  LearnPayload,
  LearnTurn,
  Lesson,
  Question,
  QuestionTurn,
  QuizTurn,
  StoredLesson,
} from "./types";

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

/** A new concept's lesson: streamed in, then kept; `learned` once Ready was pressed. */
export type LearnCard = {
  kind: "learn";
  id: string;
  conceptId: string;
  concept: string;
  lesson: StoredLesson | null;
  /** The subject has notes (from the learn turn): text with no notes citation is marked. */
  hasNotes: boolean;
  learned: boolean;
};

/** A question asked in the learn phase; `exchange` holds its answer once it has one. */
export type ExchangeCard = {
  kind: "exchange";
  id: string;
  conceptId: string;
  question: string;
  askKind: AskKind;
  exchange: Exchange | null;
};

/** The end of the learn phase: what to ask next, and Ready. */
export type ReadyCard = { kind: "ready"; id: string; conceptId: string; suggestions: string[] };

export type Card =
  | { kind: "day"; id: string; text: string }
  | { kind: "tutor"; id: string; text: string }
  | GradeCard
  | { kind: "lesson"; id: string; lesson: Lesson }
  | { kind: "notice"; id: string; text: string }
  | QuestionCard
  | LearnCard
  | ExchangeCard
  | ReadyCard
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

/** The latest question, lesson or summary: where the thread stands. */
const lastTurn = (cards: Card[]) =>
  cards.findLast((c) => c.kind === "question" || c.kind === "learn" || c.kind === "summary");

const waitingIn = (last: Card | undefined): QuestionCard | null =>
  last?.kind === "question" && last.answer === null ? last : null;

/** The question waiting for an answer, if one is. */
export function waitingQuestion(cards: Card[]): QuestionCard | null {
  return waitingIn(lastTurn(cards));
}

/** A lesson learned that the session is still paused on: the quiz hasn't heard yet. */
export function learnedCard(cards: Card[]): LearnCard | null {
  const last = lastTurn(cards);
  return last?.kind === "learn" && last.learned ? last : null;
}

/** The lesson the session is paused on, until Ready is pressed. */
export function learningCard(cards: Card[]): LearnCard | null {
  const last = lastTurn(cards);
  return last?.kind === "learn" && !last.learned ? last : null;
}

const closeDisputes = (cards: Card[]): Card[] =>
  cards.map((c) => (c.kind === "grade" && c.canDispute ? { ...c, canDispute: false } : c));

const sameQuestion = (card: QuestionCard, payload: QuestionTurn) =>
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

const questionCard = (payload: QuestionTurn): QuestionCard => ({
  kind: "question",
  id: uuid(),
  concept: payload.concept,
  number: payload.number,
  question: payload.question,
  answer: null,
  result: null,
});

const learnCard = (payload: LearnTurn): LearnCard => ({
  kind: "learn",
  id: uuid(),
  conceptId: payload.concept_id,
  concept: payload.concept,
  lesson: null,
  hasNotes: false,
  learned: false,
});

/**
 * A turn's cards, in the spec's order: grade, lesson, notices, then the question or the new
 * concept's lesson. `reconnected`: the turn the session is waiting on, fetched again rather
 * than sent in reply to an answer.
 */
export function applyTurn(cards: Card[], payload: QuizTurn, reconnected = false): Card[] {
  const last = lastTurn(cards);
  const waiting = waitingIn(last);
  const learning = payload.kind === "learn" && last?.kind === "learn" && last.conceptId === payload.concept_id;
  const next = (): Card[] => {
    if (payload.kind !== "learn") return [questionCard(payload)];
    return learning ? [] : [learnCard(payload)];
  };
  const checking = cards.findLast((c) => c.kind === "grade" && c.dispute === "checking");
  if (payload.dispute_result && checking) {
    // The re-check's result goes on the disputed card; the question is shown again (a
    // lesson under way stays where it is, its Ready card last).
    const kept = cards
      .filter((c) => c !== waiting)
      .map((c) => (c === checking ? { ...c, dispute: payload.dispute_result } : c));
    const notices = feedbackCards(null, null, payload.notices, false);
    const ready = learning ? kept.findLastIndex((c) => c.kind === "ready") : -1;
    if (ready !== -1) return [...kept.slice(0, ready), ...notices, ...kept.slice(ready)];
    return [...kept, ...notices, ...next()];
  }
  if (learning) return cards; // a reconnect: already shown
  if (payload.kind !== "learn" && waiting && sameQuestion(waiting, payload)) return cards; // likewise
  if (
    payload.kind !== "learn" &&
    reconnected &&
    last?.kind === "question" &&
    last.result === null &&
    sameQuestion(last, payload)
  ) {
    // The answer sent to it never counted (lost on the way): it is still the question waiting.
    return cards.map((c) => (c === last ? { ...c, answer: null } : c));
  }
  const before = closeDisputes(graded(cards, payload.feedback));
  const added = feedbackCards(payload.feedback, payload.lesson, payload.notices, payload.can_dispute);
  if (payload.dispute_result) {
    // Resumed in a new tab: the re-checked grade has no card yet.
    for (const c of added) if (c.kind === "grade") c.dispute = payload.dispute_result;
  }
  return [...before, ...added, ...next()];
}

const exchangeCard = (conceptId: string, exchange: Exchange, id = uuid()): ExchangeCard => ({
  kind: "exchange",
  id,
  conceptId,
  question: exchange.question,
  askKind: exchange.kind,
  exchange,
});

/**
 * The learn phase's turn: the lesson on its card, then the questions and answers, any new
 * notices, and the Ready card last. A question still waiting for its answer takes the
 * answer that came for it; one with none (dropped by the server) goes.
 */
export function applyLearn(cards: Card[], payload: LearnPayload): Card[] {
  const at = cards.findLastIndex((c) => c.kind === "learn" && c.conceptId === payload.concept_id);
  if (at === -1) return cards;
  const learn = { ...(cards[at] as LearnCard), lesson: payload.lesson, hasNotes: payload.has_notes };
  const after = cards.slice(at + 1);
  const pending = after.filter((c) => c.kind === "exchange" && c.exchange === null);
  const kept = after.filter((c) => c.kind !== "ready" && !pending.includes(c));
  const shown = kept.filter((c) => c.kind === "exchange").length;
  const fresh = payload.exchanges.slice(shown).map((e) => {
    const asked = pending.findLast((c) => c.kind === "exchange" && c.question === e.question);
    return exchangeCard(payload.concept_id, e, asked?.id);
  });
  const seen = new Set(kept.flatMap((c) => (c.kind === "notice" ? [c.text] : [])));
  const notices = payload.notices.filter((text) => !seen.has(text)); // a resumed turn repeats its own
  const ready: Card[] = payload.lesson
    ? [{ kind: "ready", id: uuid(), conceptId: payload.concept_id, suggestions: payload.suggestions }]
    : [];
  return [
    ...cards.slice(0, at),
    learn,
    ...kept,
    ...fresh,
    ...feedbackCards(null, null, notices, false),
    ...ready,
  ];
}

/** A question was asked in the learn phase: it shows, waiting, above the Ready card. */
export function asked(cards: Card[], card: ExchangeCard): Card[] {
  const ready = cards.findLastIndex((c) => c.kind === "ready" && c.conceptId === card.conceptId);
  if (ready === -1) return [...cards, card];
  return [...cards.slice(0, ready), card, ...cards.slice(ready)];
}

/** The concept is learned: its lesson stays; its unanswered questions and Ready card go. */
export function learned(cards: Card[], conceptId: string): Card[] {
  const gone = (c: Card) =>
    (c.kind === "ready" || (c.kind === "exchange" && c.exchange === null)) && c.conceptId === conceptId;
  return cards
    .filter((c) => !gone(c))
    .map((c) => (c.kind === "learn" && c.conceptId === conceptId ? { ...c, learned: true } : c));
}

/** The learner answered the waiting question: it locks, holding their answer. */
export function answered(cards: Card[], answer: Answer): Card[] {
  const waiting = waitingQuestion(cards);
  return closeDisputes(cards).map((c) => (c === waiting ? { ...c, answer } : c));
}

/** Put `originals` back in place of the cards with their ids (a reply the API refused). */
export function restored(cards: Card[], originals: Card[]): Card[] {
  const byId = new Map(originals.map((c) => [c.id, c]));
  return cards.map((c) => byId.get(c.id) ?? c);
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
