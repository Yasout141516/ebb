import type { Citation, SourceSummary, StoredLesson } from "@/lib/types";
import CitationChips, { shownCitations } from "./CitationChips";
import styles from "./LearnLesson.module.css";
import Markdown from "./Markdown";

export const LESSON_LABEL = "Lesson · quiz opens when you press Ready";
const NOTES_ONLY = "Your notes only";

/** Whether the subject has notes, as far as the lesson's sources tell. */
export function hasNotes(lesson: StoredLesson | null): boolean {
  const summary = lesson?.source_summary;
  return summary !== undefined && (summary.notes > 0 || summary.label === NOTES_ONLY);
}

/** What the header says the lesson was written from: "Your notes · pp. 12 to 15", "2 web sources". */
function sourceLines(summary: SourceSummary): string[] {
  const lines: string[] = [];
  const pages = summary.notes_pages;
  if (summary.label) lines.push(summary.label);
  else if (summary.notes > 0 && pages.length === 0) lines.push("Your notes");
  else if (summary.notes > 0 && pages.length === 1) lines.push(`Your notes · p. ${pages[0]}`);
  else if (summary.notes > 0) lines.push(`Your notes · pp. ${pages[0]} to ${pages[pages.length - 1]}`);
  if (summary.web_pages > 0) {
    lines.push(summary.web_pages === 1 ? "1 web source" : `${summary.web_pages} web sources`);
  }
  return lines;
}

/** One mark on a lesson or answer with no verified notes citation, when the subject has notes. */
export function NotInNotes({ citations, notes }: { citations: Citation[]; notes: boolean }) {
  if (!notes || shownCitations(citations).some((c) => c.kind !== "web")) return null;
  return (
    <span className={styles.badge}>
      <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
      </svg>
      Not in your notes
    </span>
  );
}

type Props = {
  label: string;
  /** The kept lesson; null while it is being written. */
  lesson: StoredLesson | null;
  /** The text streaming in, until the kept lesson replaces it. */
  live?: string | null;
  /** Asks a check-yourself question; without it the lesson is read-only. */
  onAsk?: (question: string) => void;
  /** The check-yourself questions already asked: answered below, so no button. */
  asked?: string[];
  disabled?: boolean;
};

/** A new concept's lesson, at the top of its part of the thread. */
export default function LearnLesson({ label, lesson, live = null, onAsk, asked = [], disabled = false }: Props) {
  const streaming = lesson === null && live !== null;
  return (
    <article aria-label="Lesson" className={styles.card}>
      <div className={styles.head}>
        <span className={styles.label}>{label}</span>
        {lesson && (
          <span className={styles.sources}>
            {sourceLines(lesson.source_summary).map((line, i) => (
              <span key={line} className={i === 0 && lesson.source_summary.notes > 0 ? styles.notes : styles.web}>
                {line}
              </span>
            ))}
          </span>
        )}
      </div>
      {lesson && <NotInNotes citations={lesson.citations} notes={hasNotes(lesson)} />}
      <div aria-live="polite" aria-busy={streaming}>
        {(lesson?.markdown ?? live) && <Markdown text={lesson?.markdown ?? live ?? ""} />}
        {streaming && <span className="caret" aria-hidden="true" />}
      </div>
      {lesson && lesson.checks.length > 0 && (
        <section aria-label="Check yourself" className={styles.checks}>
          <h2 className={styles.checksTitle}>Check yourself</h2>
          <p className={styles.checksNote}>Try these first. Ebb leaves them unanswered until you ask.</p>
          <ol className={styles.checkList}>
            {lesson.checks.map((check) => (
              <li key={check} className={styles.check}>
                <span className={styles.checkText}>{check}</span>
                {onAsk && !asked.includes(check) && (
                  <button
                    type="button"
                    onClick={() => onAsk(check)}
                    disabled={disabled}
                    className={`${styles.answer} hov`}
                  >
                    Answer this for me
                  </button>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
      {lesson && shownCitations(lesson.citations).length > 0 && <CitationChips citations={lesson.citations} />}
    </article>
  );
}
