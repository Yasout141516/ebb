"use client";

import { useEffect, useState } from "react";
import type { QuestionCard as Model } from "@/lib/session";
import styles from "./QuestionCard.module.css";

type Props = {
  card: Model;
  /** The waiting question, ready for an answer: keys 1–n choose, Enter submits. */
  active: boolean;
  /** A multiple-choice answer: the chosen option's position from 0. */
  onChoose: (choice: number) => void;
};

/** A question: multiple choice (radio options) or short answer (typed in the composer). */
export default function QuestionCard({ card, active, onChoose }: Props) {
  const { question } = card;
  const kind = question.type === "mcq" ? "Multiple choice" : "Short answer";
  const label = `${kind} · ${card.concept} · Q${card.number}`;
  const source = question.source_line && <p className={styles.source}>{question.source_line}</p>;

  if (question.type !== "mcq") {
    return (
      <article aria-label="Short-answer question" className={styles.card}>
        <span className={styles.label}>{label}</span>
        <h3 className={styles.prompt}>{question.prompt}</h3>
        {source}
        {card.answer && (
          <div className={styles.answer}>
            <span className={styles.answerLabel}>Your answer</span>
            <p className={styles.answerText}>{card.answer.text}</p>
          </div>
        )}
      </article>
    );
  }
  return <Choice card={card} label={label} source={source} active={active} onChoose={onChoose} />;
}

function Choice({
  card,
  label,
  source,
  active,
  onChoose,
}: Props & { label: string; source: React.ReactNode }) {
  const [pick, setPick] = useState<number | null>(null);
  const { options } = card.question;
  const answer = card.answer;
  const locked = answer !== null || !active;
  const chosen = answer ? answer.choice : pick;

  // The keys work wherever focus is, except where they type or press something else.
  useEffect(() => {
    if (locked) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest("textarea, select, button, a, [role=dialog], [contenteditable=true]")) return;
      if (target instanceof HTMLInputElement && target.type !== "radio") return;
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= options.length) {
        e.preventDefault();
        setPick(n - 1);
      } else if (e.key === "Enter" && pick !== null) {
        e.preventDefault();
        onChoose(pick);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [locked, options.length, pick, onChoose]);

  const optionClass = (i: number) => {
    let state = "";
    if (i === chosen && card.result) state = card.result === "correct" ? styles.right : styles.wrong;
    else if (i === chosen) state = styles.picked;
    return `${styles.option} ${state} ${locked ? styles.locked : ""}`;
  };

  return (
    <article aria-label="Multiple-choice question" className={styles.card}>
      <div className={styles.top}>
        <span className={styles.label}>{label}</span>
        <span className={styles.hint}>
          Press <kbd className={styles.kbd}>1</kbd>–<kbd className={styles.kbd}>{options.length}</kbd> to
          choose, <kbd className={styles.kbd}>Enter</kbd> to submit
        </span>
      </div>
      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{card.question.prompt}</legend>
        {source}
        {options.map((text, i) => (
          <label key={i} className={optionClass(i)}>
            <input
              type="radio"
              name={`question-${card.id}`}
              checked={chosen === i}
              onChange={() => setPick(i)}
              aria-label={answer && i === chosen ? `${text}, your answer` : text}
              disabled={locked}
              className={styles.radio}
            />
            <span aria-hidden="true" className={styles.number}>
              {i + 1}
            </span>
            <span className={styles.text}>{text}</span>
            <span className={styles.tag}>{answer && i === chosen ? "Your answer" : ""}</span>
          </label>
        ))}
      </fieldset>
      {!answer && (
        <div className={styles.submitRow}>
          <button
            type="button"
            onClick={() => pick !== null && onChoose(pick)}
            disabled={locked || pick === null}
            className={`${styles.submit} pbtn`}
          >
            Check answer
          </button>
        </div>
      )}
    </article>
  );
}
