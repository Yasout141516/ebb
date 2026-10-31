"use client";

import { Fragment, useEffect, useRef } from "react";
import ErrorCard from "@/components/ErrorCard";
import { EXHAUSTED_NOTICE as EXHAUSTED } from "@/lib/notices";
import type { Card, QuestionCard as QuestionModel } from "@/lib/session";
import Composer from "./Composer";
import GradeCard from "./GradeCard";
import LearnExchange, { SuggestedNext } from "./LearnExchange";
import LearnLesson, { LESSON_LABEL } from "./LearnLesson";
import LessonCard from "./LessonCard";
import NoticeDivider from "./NoticeDivider";
import QuestionCard from "./QuestionCard";
import ReadyCard from "./ReadyCard";
import ResumeCard from "./ResumeCard";
import StepRow from "./StepRow";
import SummaryCard from "./SummaryCard";
import styles from "./Thread.module.css";
import type { Study } from "./useStudy";

type Props = {
  study: Study;
  /** Dispute grade was pressed on that grade card. */
  onDispute: (gradeId: string) => void;
};

/**
 * A grade that failed brings its question back: the failure's Retry sends the same answer
 * to it again. Null unless the latest turn (its failure notice at `failure`) is such a one.
 */
function failedAnswer(cards: Card[], waiting: QuestionModel | null, failure: number): QuestionModel | null {
  if (!waiting) return null;
  const at = cards.findLastIndex((c) => c.kind === "question" && c.answer !== null);
  const tried = cards[at];
  if (tried?.kind !== "question" || failure < at) return null;
  return tried.result === null && tried.question.prompt === waiting.question.prompt ? tried : null;
}

/** The middle column: the session's thread of cards, then the composer. */
export default function Thread({ study, onDispute }: Props) {
  const { cards, waiting, learning } = study;
  const end = useRef<HTMLDivElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const failure = useRef<HTMLDivElement>(null);
  const readied = useRef(false); // Ready was pressed: focus goes to the first question
  const lastFailure = cards.findLastIndex((c) => c.kind === "notice" && c.text === EXHAUSTED);
  const retried = failedAnswer(cards, waiting, lastFailure);
  // Where the text streaming in goes: the question being answered, else the lesson being written.
  const answering = study.live === null ? null : cards.findLast((c) => c.kind === "exchange" && c.exchange === null);
  const streamingTo = answering?.id ?? (study.live !== null && learning && !learning.lesson ? learning.id : null);
  // A lesson or answer arriving (the Ready card moving under it) doesn't scroll past its start.
  const scrollKey = cards.filter((c) => c.kind !== "ready").length;

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [scrollKey, study.step, study.error, study.offer]);

  useEffect(() => {
    if (study.error?.focus) failure.current?.querySelector("button")?.focus();
  }, [study.error]);

  useEffect(() => {
    if (!readied.current || !waiting) return;
    readied.current = false;
    // A short answer's composer takes focus itself.
    thread.current?.querySelector<HTMLElement>("input[type=radio]:not(:disabled)")?.focus();
  }, [waiting]);

  /** The concept's lesson card, for what its questions and answers need of it. */
  const lessonOf = (conceptId: string) => cards.findLast((c) => c.kind === "learn" && c.conceptId === conceptId);

  function render(card: Card, index: number) {
    switch (card.kind) {
      case "day":
        // The thread's header: its day and time, and End session while the session is open.
        return (
          <div className={styles.dayRow}>
            <p className={styles.day}>{card.text}</p>
            {study.open && (
              <button
                type="button"
                onClick={study.end}
                disabled={study.running}
                className={`${styles.endButton} hov`}
              >
                End session
              </button>
            )}
          </div>
        );
      case "tutor":
        return (
          <div className={styles.tutor}>
            <span className={styles.name}>Ebb</span>
            <p className={styles.text}>{card.text}</p>
          </div>
        );
      case "grade":
        return <GradeCard card={card} onDispute={onDispute} />;
      case "lesson":
        return <LessonCard lesson={card.lesson} />;
      case "notice":
        if (card.text !== EXHAUSTED) return <NoticeDivider text={card.text} />;
        return (
          <ErrorCard
            title={card.text}
            action={
              retried?.answer && index === lastFailure
                ? { label: "Retry", onClick: () => study.answer(retried.answer!.text, retried.answer!.choice) }
                : undefined
            }
          />
        );
      case "question":
        return (
          <QuestionCard
            card={card}
            active={waiting?.id === card.id}
            onChoose={(choice) => study.answer("", choice)}
          />
        );
      case "learn": {
        // Learned in another tab, its lesson never loaded here: nothing to show, not an empty card.
        if (card.learned && card.lesson === null) return null;
        const open = learning?.id === card.id;
        const asked = cards.flatMap((c) =>
          c.kind === "exchange" && c.conceptId === card.conceptId && c.askKind === "check" ? [c.question] : [],
        );
        return (
          <LearnLesson
            label={card.learned ? "Lesson" : LESSON_LABEL}
            lesson={card.lesson}
            live={streamingTo === card.id ? study.live : null}
            hasNotes={card.hasNotes}
            onAsk={open ? (question) => study.ask(question, "check") : undefined}
            asked={asked}
            disabled={study.running}
          />
        );
      }
      case "exchange": {
        const lesson = lessonOf(card.conceptId);
        return (
          <LearnExchange
            question={card.question}
            exchange={card.exchange}
            live={streamingTo === card.id ? study.live : null}
            hasNotes={lesson?.kind === "learn" && lesson.hasNotes}
          />
        );
      }
      case "ready":
        return (
          <>
            <SuggestedNext
              suggestions={card.suggestions}
              onAsk={(question) => study.ask(question, "suggested")}
              disabled={study.running}
            />
            <ReadyCard
              onReady={() => {
                readied.current = true;
                study.ready();
              }}
              disabled={study.running}
            />
          </>
        );
      case "summary":
        return <SummaryCard finished={card.finished} />;
    }
  }

  return (
    <main aria-label="Study thread" className={styles.main}>
      <div ref={thread} className={styles.thread}>
        {cards.map((card, i) => (
          <Fragment key={card.id}>{render(card, i)}</Fragment>
        ))}
        {study.offer && (
          <ResumeCard offer={study.offer} onResume={study.resume} onStartFresh={study.startFresh} />
        )}
        {study.step && <StepRow label={study.step} />}
        {study.error && (
          <div ref={failure}>
            <ErrorCard
              title={study.error.message}
              action={study.error.retry ? { label: "Retry", onClick: study.error.retry } : undefined}
            />
          </div>
        )}
        <div ref={end} className={styles.end} />
      </div>
      {learning ? (
        <Composer
          key="learn"
          enabled={learning.lesson !== null && !study.running}
          autoFocus={!study.error}
          label="Your question"
          placeholder={`Ask your own question about ${learning.concept}`}
          onSend={(text) => study.ask(text, "typed")}
        />
      ) : (
        <Composer
          key="quiz"
          enabled={waiting !== null && waiting.question.type !== "mcq"}
          onSend={(text) => study.answer(text, null)}
        />
      )}
    </main>
  );
}
