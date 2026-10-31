"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { ApiError, getJson, messageOf, postJson, postOnLeave } from "@/lib/api";
import { clockTime } from "@/lib/format";
import { FALLBACK_NOTICE } from "@/lib/notices";
import {
  activeThread,
  answered,
  applyFinished,
  applyTurn,
  type Card,
  disputeSent,
  driverId,
  forgetThread,
  type LearnCard,
  learnedCard,
  learningCard,
  loadThread,
  type QuestionCard,
  rememberThread,
  restored,
  saveThread,
  uuid,
  waitingQuestion,
} from "@/lib/session";
import { streamPost, type SseEvent } from "@/lib/sse";
import type { AskKind, DisputeReason, Finished, Offer, QuizTurn, SessionOffer, Started, StreamError } from "@/lib/types";
import useLearn from "./useLearn";

/** How often an open session renews this tab's lease on it. */
const HEARTBEAT_MS = 30_000;
/** Shown while this tab waits for its own last turn, still running on the server. */
const FINISHING = "Finishing your last answer…";
/** How long a reconnect waits for that turn before offering the session instead. */
const FINISHING_MS = 30_000;

type Streaming = { refused?: (error: ApiError) => boolean; step?: string | null; resumed?: boolean };

/**
 * A failure shown as the error card; `retry` sends the failed request again. `focus`: text
 * it was writing was wiped, so focus moves to Retry.
 */
export type StudyError = { message: string; retry: (() => void) | null; focus?: boolean };

export type Study = {
  cards: Card[];
  /** An open session to resume, shown as the Resume card. */
  offer: Offer | null;
  /** The step the tutor is on, until its cards arrive. */
  step: string | null;
  error: StudyError | null;
  /** A configuration problem: the tutor can't run at all; shown full width. */
  fatal: string | null;
  /** A request is in flight. */
  running: boolean;
  /** A turn said the tutor switched to its backup model. */
  backup: boolean;
  /** The question waiting for an answer, if one is and nothing is in flight. */
  waiting: QuestionCard | null;
  /** The new concept's lesson the session is paused on, until Ready is pressed. */
  learning: LearnCard | null;
  /** The lesson or answer streaming in, until its turn replaces it. */
  live: string | null;
  /** The concepts learned in this tab, whose lessons can be read again. */
  learned: string[];
  /** The session is under way: its End session button shows. */
  open: boolean;
  /** Counts the turns and endings so far: memory levels may have changed with each. */
  updates: number;
  answer: (text: string, choice: number | null) => void;
  dispute: (gradeId: string, reason: DisputeReason, explanation: string) => void;
  /** Ask a question about the lesson being learned. */
  ask: (question: string, kind: AskKind) => void;
  /** I'm ready, quiz me. */
  ready: () => void;
  end: () => void;
  resume: () => void;
  startFresh: () => void;
};

const dayCard = (): Card => ({ kind: "day", id: uuid(), text: `Today · ${clockTime(new Date())}` });

/**
 * One subject's study session in this tab: opening it (reconnect, Resume card or a new
 * start), its streamed turns, and the learner's replies. Every request that fails can be
 * sent again as it was, so a resent answer keeps its attempt id. A turn that pauses on a
 * new concept hands over to useLearn until the concept is learned.
 */
export default function useStudy(subjectId: string): Study {
  const [cards, setCards] = useState<Card[]>([]);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<StudyError | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [updates, setUpdates] = useState(0);
  const thread = useRef<string | null>(null); // the open session's id, once known
  const begun = useRef<string | null>(null); // the lesson card whose learn phase was started
  const inFlight = useRef<AbortController | null>(null);
  const learn = useLearn(setCards, { onFatal: setFatal });

  useEffect(() => {
    if (thread.current) saveThread(thread.current, cards);
  }, [cards]);

  /** A new request, showing `step` until the API names one; the one before it (if any) is abandoned. */
  function begin(step: string | null = null): AbortSignal {
    inFlight.current?.abort();
    // A lesson that never came (its start failed): it starts again once this request is done.
    if (learningCard(cards)?.lesson === null) begun.current = null;
    learn.cancel();
    const controller = new AbortController();
    inFlight.current = controller;
    setRunning(true);
    setError(null);
    setStep(step);
    return controller.signal;
  }

  function settle(signal: AbortSignal) {
    if (inFlight.current?.signal !== signal) return;
    inFlight.current = null;
    setRunning(false);
    setStep(null);
  }

  function onEvent(event: SseEvent, retry: () => void, resumed: boolean) {
    switch (event.event) {
      case "started": {
        const { thread_id, greeting } = event.data as Started;
        thread.current = thread_id;
        rememberThread(subjectId, thread_id);
        setCards((c) => [...c, { kind: "tutor", id: uuid(), text: greeting }]);
        break;
      }
      case "step":
        setStep((event.data as { label: string }).label);
        break;
      case "turn":
        setStep(null);
        setCards((c) => applyTurn(c, (event.data as { payload: QuizTurn }).payload, resumed));
        setUpdates((n) => n + 1);
        break;
      case "finished":
        setStep(null);
        setCards((c) => applyFinished(c, event.data as Finished));
        setUpdates((n) => n + 1);
        forgetThread(subjectId);
        thread.current = null;
        break;
      case "error": {
        setStep(null);
        // R33: {notice} is worth trying again; {error} is the tutor's setup, shown full width.
        const data = event.data as StreamError;
        if ("notice" in data) setError({ message: data.notice, retry });
        else setFatal(data.error);
        break;
      }
    }
  }

  /**
   * POST and read the events, showing `step` until the API names one. A refusal (4xx) goes
   * to `refused` first, which returns true if it dealt with it; otherwise it shows as the
   * error card, without a Retry that would only be refused again. Any other failure's error
   * card retries with `retry`. `resumed`: the request fetches the session's waiting turn.
   */
  async function stream(
    path: string,
    body: unknown,
    retry: () => void,
    { refused, step = null, resumed = false }: Streaming = {},
  ): Promise<void> {
    const signal = begin(step);
    try {
      await streamPost(path, body, (e) => onEvent(e, retry, resumed), signal);
    } catch (e) {
      if (signal.aborted) return;
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        if (!refused?.(e)) setError({ message: e.message, retry: null });
        return;
      }
      setError({ message: messageOf(e), retry });
    } finally {
      settle(signal);
    }
  }

  function start() {
    setOffer(null);
    thread.current = null;
    setCards([dayCard()]);
    // Once `started` has named the session, carry on with it rather than start another.
    const retry = () => (thread.current ? reconnect(thread.current, false) : start());
    void stream(`/subjects/${subjectId}/session`, { driver: driverId() }, retry);
  }

  /**
   * Carry on with a session; `silent`: this tab's own, which may have ended meanwhile. Its
   * last turn may also still be running on the server (the tab was refreshed or left while
   * an answer was graded), refused as busy until it is done: so a silent reconnect tries
   * again, `delay` ms later and less often each time, until `until`, then offers the session.
   */
  function reconnect(threadId: string, silent: boolean, until = Date.now() + FINISHING_MS, delay = 1_000) {
    const retry = () => reconnect(threadId, silent);
    const gone = (e: ApiError) => {
      if (e.status !== 404 && e.status !== 409) return false;
      if (e.status === 409 && Date.now() + delay <= until) {
        void finishing(() => reconnect(threadId, true, until, Math.min(delay * 2, 5_000)), delay);
        return true;
      }
      forgetThread(subjectId);
      thread.current = null;
      setCards([]);
      void lookForOffer();
      return true;
    };
    const again = silent && delay > 1_000; // already waiting: the step line stays
    void stream(`/sessions/${threadId}/resume`, { driver: driverId() }, retry, {
      refused: silent ? gone : undefined,
      step: again ? FINISHING : null,
      resumed: true,
    });
  }

  /** Show that this tab's last turn is finishing, then carry on (`then`) `delay` ms later. */
  async function finishing(then: () => void, delay: number) {
    const signal = begin(FINISHING);
    await new Promise<void>((done) => {
      const timer = setTimeout(done, delay);
      signal.addEventListener("abort", () => {
        clearTimeout(timer);
        done();
      });
    });
    if (!signal.aborted) then();
  }

  async function lookForOffer() {
    const signal = begin();
    try {
      const driver = encodeURIComponent(driverId());
      const found = await getJson<SessionOffer>(`/subjects/${subjectId}/session?driver=${driver}`, signal);
      if (signal.aborted) return;
      settle(signal);
      if (found.offer) setOffer(found.offer);
      else start();
    } catch (e) {
      if (signal.aborted) return;
      settle(signal);
      setError({ message: messageOf(e), retry: () => void lookForOffer() });
    }
  }

  function open() {
    const saved = activeThread(subjectId);
    const savedCards = saved ? loadThread(saved) : null;
    if (saved && savedCards) {
      thread.current = saved;
      setCards(savedCards);
      reconnect(saved, true);
      return;
    }
    if (saved) forgetThread(subjectId);
    void lookForOffer();
  }

  const opening = useEffectEvent(open);
  useEffect(() => {
    // Deferred, so a development double mount doesn't start two sessions.
    const timer = setTimeout(opening, 0);
    return () => {
      clearTimeout(timer);
      inFlight.current?.abort();
    };
  }, []);

  function resume() {
    if (!offer) return;
    const threadId = offer.thread_id;
    setOffer(null);
    thread.current = threadId;
    rememberThread(subjectId, threadId);
    setCards([dayCard()]);
    reconnect(threadId, false);
  }

  async function discardAndStart(old: Offer) {
    const signal = begin();
    try {
      await postJson(`/sessions/${old.thread_id}/discard`, { driver: driverId() }, signal);
    } catch (e) {
      if (signal.aborted) return;
      settle(signal);
      setError({ message: messageOf(e), retry: () => void discardAndStart(old) });
      return;
    }
    settle(signal);
    start();
  }

  function startFresh() {
    if (!offer) return;
    const old = offer;
    setOffer(null);
    void discardAndStart(old);
  }

  /** Another tab or terminal took the session over: only Start fresh is left. */
  function takenOver(threadId: string) {
    const last = cards.findLast((c) => c.kind === "question");
    forgetThread(subjectId);
    thread.current = null;
    setOffer({
      thread_id: threadId,
      concept: last?.concept ?? "",
      number: last?.number ?? 1,
      last_active_at: new Date().toISOString(),
      busy: true,
    });
  }

  /**
   * Send a reply to the open session; Retry sends exactly the same reply again. If the API
   * refuses it, the cards it changed (`originals`, as they were before) are put back.
   */
  function reply(body: Record<string, unknown>, originals: Card[] = []) {
    const threadId = thread.current;
    if (!threadId) return;
    const sent = { driver: driverId(), reply: body };
    const refused = (e: ApiError) => {
      setCards((c) => restored(c, originals));
      if (e.status !== 409) return false;
      takenOver(threadId);
      return true;
    };
    const send = () => void stream(`/sessions/${threadId}/reply`, sent, send, { refused });
    send();
  }

  const busy = running || learn.running;
  const finished = cards.some((c) => c.kind === "summary");
  const waiting = busy || offer ? null : waitingQuestion(cards);
  const learning = offer ? null : learningCard(cards);
  const sessionOpen =
    !finished && offer === null && cards.some((c) => c.kind === "question" || c.kind === "learn");

  // A turn paused on a new concept: once the quiz's request is done, its lesson starts (or,
  // after a refresh, carries on: the kept lesson comes back without a model call).
  const startLearning = useEffectEvent((conceptId: string) => learn.start(conceptId));
  const learningId = learning?.id ?? null;
  const learningConcept = learning?.conceptId ?? null;
  useEffect(() => {
    if (!learningId || !learningConcept || running || begun.current === learningId) return;
    begun.current = learningId;
    startLearning(learningConcept);
  }, [learningId, learningConcept, running]);

  // The concept is learned but the quiz is still paused on it (just now, or the reply was
  // lost to a refresh or a failure): tell the session, once per lesson card; Retry resends.
  const handed = useRef<string | null>(null);
  const handOver = useEffectEvent(() => reply({ learned: true }));
  const learnedId = offer ? null : (learnedCard(cards)?.id ?? null);
  const idle = !running && !learn.running && error === null;
  useEffect(() => {
    if (!learnedId || !idle || handed.current === learnedId) return;
    handed.current = learnedId;
    handOver();
  }, [learnedId, idle]);

  /**
   * Renew this tab's lease while the session is open; a request in flight renews it
   * itself, and a beat still waiting for its answer isn't doubled. 404: the session has
   * ended meanwhile, nothing to do. 409: another tab or terminal has taken it over, as a
   * refused reply would say.
   */
  const beat = useRef<AbortController | null>(null);
  const heartbeat = useEffectEvent(async () => {
    const threadId = thread.current;
    if (!threadId || inFlight.current || beat.current) return;
    const controller = new AbortController();
    beat.current = controller;
    try {
      await postJson(`/sessions/${threadId}/heartbeat`, { driver: driverId() }, controller.signal);
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 409 && thread.current === threadId && !inFlight.current) {
        takenOver(threadId);
      }
      // anything else: the next beat tries again
    } finally {
      if (beat.current === controller) beat.current = null;
    }
  });
  useEffect(() => {
    if (!sessionOpen) return;
    const timer = setInterval(() => void heartbeat(), HEARTBEAT_MS);
    return () => {
      clearInterval(timer);
      beat.current?.abort();
      beat.current = null;
    };
  }, [sessionOpen]);

  // A closed tab lets go of its session, so another tab may take it up at once.
  const leaving = useEffectEvent(() => {
    const threadId = thread.current;
    if (threadId) postOnLeave(`/sessions/${threadId}/release?driver=${encodeURIComponent(driverId())}`);
  });
  useEffect(() => {
    const onPageHide = () => leaving();
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, []);

  function answer(text: string, choice: number | null) {
    if (!waiting) return;
    const shown = choice === null ? text : waiting.question.options[choice];
    const attempt = { attempt_id: uuid(), text: shown, choice };
    const changed = cards.filter((c) => c === waiting || (c.kind === "grade" && c.canDispute));
    setCards((c) => answered(c, attempt));
    reply({ attempt_id: attempt.attempt_id, answer: choice === null ? text : String(choice + 1) }, changed);
  }

  function dispute(gradeId: string, reason: DisputeReason, explanation: string) {
    if (busy) return;
    setCards((c) => disputeSent(c, gradeId));
    reply({ dispute: { id: uuid(), reason, explanation } }, cards.filter((c) => c.id === gradeId));
  }

  function end() {
    if (!busy) reply({ quit: true });
  }

  function ask(question: string, kind: AskKind) {
    if (learning?.lesson && !busy) learn.ask(learning.conceptId, question, kind);
  }

  function ready() {
    if (learning?.lesson && !busy) learn.ready(learning.conceptId);
  }

  return {
    cards,
    offer,
    step: step ?? learn.step,
    error: error ?? learn.error,
    fatal,
    running: busy,
    backup: cards.some((c) => c.kind === "notice" && c.text.startsWith(FALLBACK_NOTICE)),
    waiting,
    learning,
    live: learn.live,
    learned: cards.flatMap((c) => (c.kind === "learn" && c.learned ? [c.conceptId] : [])),
    open: sessionOpen,
    updates,
    answer,
    dispute,
    ask,
    ready,
    end,
    resume,
    startFresh,
  };
}
