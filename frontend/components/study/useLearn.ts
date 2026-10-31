"use client";

import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from "react";
import { ApiError, messageOf } from "@/lib/api";
import { applyLearn, asked, type Card, type ExchangeCard, learned, uuid } from "@/lib/session";
import { streamPost, type SseEvent } from "@/lib/sse";
import type { AskKind, LearnPayload, LearnReply, StreamError } from "@/lib/types";
import type { StudyError } from "./useStudy";

export type Learn = {
  /** The lesson or answer streaming in now, until its turn replaces it with the kept text. */
  live: string | null;
  step: string | null;
  error: StudyError | null;
  running: boolean;
  /** Start the concept's learn phase, or carry on with it: the kept lesson comes back as it is. */
  start: (conceptId: string) => void;
  ask: (conceptId: string, question: string, kind: AskKind) => void;
  ready: (conceptId: string) => void;
  /** Stop any call in flight and clear what it showed (the quiz takes over). */
  cancel: () => void;
};

type Options = {
  /** A configuration problem, shown full width. */
  onFatal: (message: string) => void;
};

/**
 * A new concept's learn phase, for useStudy: its lesson and the learner's questions about
 * it, each call streaming its text into the thread, then Ready. Every call ends with one
 * `learn` turn (the kept lesson and answers replace the streamed text) or `learned`, which
 * marks the lesson card (useStudy then hands over to the quiz). A failed call can be sent
 * again as it was, so a retried question keeps its ask id.
 */
export default function useLearn(setCards: Dispatch<SetStateAction<Card[]>>, { onFatal }: Options): Learn {
  const [live, setLive] = useState<string | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<StudyError | null>(null);
  const [running, setRunning] = useState(false);
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => () => inFlight.current?.abort(), []);

  /**
   * POST and read the call's events. A refusal (4xx) goes to `refused` first, which returns
   * true if it dealt with it; otherwise it shows without a Retry. Any other failure shows
   * the error card, whose Retry is `retry`.
   */
  async function stream(path: string, body: unknown, retry: () => void, refused?: (e: ApiError) => boolean) {
    inFlight.current?.abort();
    const controller = new AbortController();
    const signal = controller.signal;
    inFlight.current = controller;
    setRunning(true);
    setError(null);
    setStep(null);
    setLive(null);
    let shown = false; // text was on screen, then wiped: focus goes to Retry
    const onEvent = (event: SseEvent) => {
      switch (event.event) {
        case "step":
          setStep((event.data as { label: string }).label);
          break;
        case "text":
          shown = true;
          setStep(null);
          setLive((text) => (text ?? "") + (event.data as { delta: string }).delta);
          break;
        case "discard":
          setLive(null);
          break;
        case "learn":
          setLive(null);
          setStep(null);
          setCards((c) => applyLearn(c, (event.data as { payload: LearnPayload }).payload));
          break;
        case "learned":
          setLive(null);
          setStep(null);
          setCards((c) => learned(c, (event.data as { concept_id: string }).concept_id));
          break;
        case "error": {
          setLive(null);
          setStep(null);
          const data = event.data as StreamError;
          if ("notice" in data) setError({ message: data.notice, retry, focus: shown });
          else onFatal(data.error);
          break;
        }
      }
    };
    try {
      await streamPost(path, body, onEvent, signal);
    } catch (e) {
      if (signal.aborted) return;
      setLive(null); // cut off: nothing partial stays on screen
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        if (!refused?.(e)) setError({ message: e.message, retry: null });
      } else {
        setError({ message: messageOf(e), retry, focus: shown });
      }
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setRunning(false);
        setStep(null);
      }
    }
  }

  function start(conceptId: string) {
    const send = () => {
      // Busy (still finishing in a tab just closed) can be tried again.
      const refused = (e: ApiError) => {
        if (e.status !== 409) return false;
        setError({ message: e.message, retry: send });
        return true;
      };
      void stream(`/concepts/${conceptId}/learn`, {}, send, refused);
    };
    send();
  }

  function reply(conceptId: string, body: LearnReply, refused?: (e: ApiError) => boolean) {
    const sent = { reply: body };
    const send = () => void stream(`/concepts/${conceptId}/learn/reply`, sent, send, refused);
    send();
  }

  function ask(conceptId: string, question: string, kind: AskKind) {
    const card: ExchangeCard = { kind: "exchange", id: uuid(), conceptId, question, askKind: kind, exchange: null };
    setCards((c) => asked(c, card));
    // Refused: the question was never asked, so its bubble goes.
    reply(conceptId, { ask: question, kind, ask_id: uuid() }, () => {
      setCards((c) => c.filter((x) => x.id !== card.id));
      return false;
    });
  }

  function ready(conceptId: string) {
    reply(conceptId, { ready: true });
  }

  function cancel() {
    inFlight.current?.abort();
    inFlight.current = null;
    setRunning(false);
    setStep(null);
    setLive(null);
    setError(null);
  }

  return { live, step, error, running, start, ask, ready, cancel };
}
