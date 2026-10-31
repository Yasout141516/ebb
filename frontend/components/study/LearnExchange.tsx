import type { Exchange } from "@/lib/types";
import CitationChips, { shownCitations } from "./CitationChips";
import styles from "./LearnExchange.module.css";
import { NotInNotes } from "./LearnLesson";
import Markdown from "./Markdown";

type Props = {
  question: string;
  /** Its answer, once kept; null while it is being written (or after it failed). */
  exchange: Exchange | null;
  /** The answer streaming in, until the kept one replaces it. */
  live?: string | null;
  /** Whether the subject has notes: an answer with no notes citation is then marked. */
  hasNotes: boolean;
};

/** A question asked in the learn phase (the learner's bubble) and Ebb's answer under it. */
export default function LearnExchange({ question, exchange, live = null, hasNotes }: Props) {
  const text = exchange?.answer ?? live;
  return (
    <>
      <p className={styles.question}>{question}</p>
      {text !== null && (
        <div className={styles.answer}>
          <span className={styles.head}>
            <span className={styles.name}>Ebb</span>
            {exchange && <NotInNotes citations={exchange.citations} notes={hasNotes} />}
          </span>
          <div aria-live="polite" aria-busy={exchange === null}>
            <Markdown text={text} compact />
            {exchange === null && <span className="caret" aria-hidden="true" />}
          </div>
          {exchange && shownCitations(exchange.citations).length > 0 && (
            <CitationChips citations={exchange.citations} />
          )}
        </div>
      )}
    </>
  );
}

type SuggestedProps = {
  suggestions: string[];
  onAsk: (question: string) => void;
  disabled?: boolean;
};

/** "Suggested next": follow-up questions as chips; tapping one asks it. */
export function SuggestedNext({ suggestions, onAsk, disabled = false }: SuggestedProps) {
  if (suggestions.length === 0) return null;
  return (
    <section aria-label="Suggested follow-ups" className={styles.suggested}>
      <span className={styles.suggestedLabel}>Suggested next</span>
      <div className={styles.chips}>
        {suggestions.map((s) => (
          <button key={s} type="button" onClick={() => onAsk(s)} disabled={disabled} className={`${styles.chip} hov`}>
            {s}
          </button>
        ))}
      </div>
    </section>
  );
}
