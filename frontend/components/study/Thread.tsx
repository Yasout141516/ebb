"use client";

import { Fragment, useEffect, useRef } from "react";
import ErrorCard from "@/components/ErrorCard";
import type { Card, QuestionCard as QuestionModel } from "@/lib/session";
import Composer from "./Composer";
import GradeCard from "./GradeCard";
import LessonCard from "./LessonCard";
import NoticeDivider from "./NoticeDivider";
import QuestionCard from "./QuestionCard";
import ResumeCard from "./ResumeCard";
import StepRow from "./StepRow";
import SummaryCard from "./SummaryCard";
import styles from "./Thread.module.css";
import type { Study } from "./useStudy";

const EXHAUSTED = "The tutor couldn't respond — your progress is saved. Try again in a minute.";

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
  if (!waiting || failure < 0 || failure < cards.findLastIndex((c) => c.kind === "user")) return null;
  const tried = cards.findLast((c): c is QuestionModel => c.kind === "question" && c.answer !== null);
  return tried && tried.result === null && tried.question.prompt === waiting.question.prompt ? tried : null;
}

/** The middle column: the session's thread of cards, then the composer. */
export default function Thread({ study, onDispute }: Props) {
  const { cards, waiting } = study;
  const end = useRef<HTMLDivElement>(null);
  const lastFailure = cards.findLastIndex((c) => c.kind === "notice" && c.text === EXHAUSTED);
  const retried = failedAnswer(cards, waiting, lastFailure);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [cards.length, study.step, study.error, study.offer]);

  function render(card: Card, index: number) {
    switch (card.kind) {
      case "day":
        return <p className={styles.day}>{card.text}</p>;
      case "tutor":
        return (
          <div className={styles.tutor}>
            <span className={styles.name}>Ebb</span>
            <p className={styles.text}>{card.text}</p>
          </div>
        );
      case "user":
        return <div className={styles.user}>{card.text}</div>;
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
      case "summary":
        return <SummaryCard finished={card.finished} />;
    }
  }

  return (
    <main aria-label="Study thread" className={styles.main}>
      {study.open && (
        <div className={styles.bar}>
          <div className={styles.barInner}>
            <button
              type="button"
              onClick={study.end}
              disabled={waiting === null}
              className={`${styles.endButton} hov`}
            >
              End session
            </button>
          </div>
        </div>
      )}
      <div className={styles.thread}>
        {cards.map((card, i) => (
          <Fragment key={card.id}>{render(card, i)}</Fragment>
        ))}
        {study.offer && (
          <ResumeCard offer={study.offer} onResume={study.resume} onStartFresh={study.startFresh} />
        )}
        {study.step && <StepRow label={study.step} />}
        {study.error && (
          <ErrorCard
            title={study.error.message}
            action={study.error.retry ? { label: "Retry", onClick: study.error.retry } : undefined}
          />
        )}
        <div ref={end} />
      </div>
      <Composer
        enabled={waiting !== null && waiting.question.type !== "mcq"}
        onSend={(text) => study.answer(text, null)}
      />
    </main>
  );
}
