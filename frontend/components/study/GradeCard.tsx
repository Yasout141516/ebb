import { stageDots } from "@/lib/format";
import type { GradeCard as Model } from "@/lib/session";
import type { DisputeResult, GradeValue } from "@/lib/types";
import CitationChips, { shownCitations } from "./CitationChips";
import styles from "./GradeCard.module.css";

const NAME: Record<GradeValue, string> = { correct: "Correct", partial: "Partial", incorrect: "Incorrect" };
const RANK: Record<GradeValue, number> = { incorrect: 0, partial: 1, correct: 2 };

/** A re-check's outcome in the CLI's words (backend ui.dispute_line), the failure in the spec's. */
export function disputeLine(result: DisputeResult): string {
  switch (result.outcome) {
    case "refused":
      return "Only short-answer grades from this session can be disputed.";
    case "failed":
      return "Couldn't re-check right now — the grade stands.";
    case "upheld":
      return `Re-check: the grade stands — ${result.explanation}`;
    case "voided":
      return "Re-check: the question was ambiguous — that answer no longer counts.";
    case "changed": {
      const now = result.new_grade ?? result.original_grade;
      const raised = RANK[now] > RANK[result.original_grade];
      const [verb, link] = raised ? ["raised to", "back to"] : ["lowered to", "now"];
      return `Re-check: ${verb} ${NAME[now]} — ${result.concept} ${link} ${stageDots(result.stage)} level ${result.stage}.`;
    }
  }
}

function VerdictIcon({ value }: { value: GradeValue }) {
  if (value === "correct") {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className={styles.stroke}>
        <path d="m5 12.5 4.5 4.5L19 7.5" />
      </svg>
    );
  }
  if (value === "partial") {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="8" className={styles.ring} />
        <path d="M12 4a8 8 0 0 1 0 16z" className={styles.half} />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className={styles.stroke}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** The general-knowledge label, where a grade has no source to cite. */
export function GeneralKnowledge() {
  return (
    <span className={styles.general}>
      <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
      </svg>
      General knowledge · not from your notes
    </span>
  );
}

type Props = { card: Model; onDispute: (gradeId: string) => void };

/** The grade for an answer: verdict, explanation, sources, and any dispute of it. */
export default function GradeCard({ card, onDispute }: Props) {
  const { grade, dispute } = card;
  const result = dispute === "checking" ? null : dispute;
  const value = result?.outcome === "changed" && result.new_grade ? result.new_grade : grade.value;
  const sources = shownCitations(grade.citations);
  const fromNotes = sources.some((c) => c.kind !== "web");
  const recheckDone = result !== null && result.outcome !== "refused" && result.outcome !== "failed";
  return (
    <article aria-label={`Grade: ${NAME[value]}`} className={styles.card}>
      <div className={`${styles.head} ${styles[value]}`}>
        <span className={styles.verdict}>
          <VerdictIcon value={value} />
          {NAME[value]}
        </span>
      </div>
      <div className={styles.body}>
        <p className={styles.explanation}>{grade.explanation}</p>
        {sources.length > 0 ? (
          <CitationChips citations={grade.citations} />
        ) : (
          grade.general_knowledge && <GeneralKnowledge />
        )}
        {dispute === "checking" && (
          <p role="status" className={styles.checking}>
            Dispute sent. A second grader is re-checking your answer…
          </p>
        )}
        {result && (
          <p role="status" className={styles.resolved}>
            {recheckDone && <strong className={styles.strong}>Re-check complete.</strong>}
            {recheckDone && " "}
            {disputeLine(result)}
          </p>
        )}
        {result && "citations" in result && <CitationChips citations={result.citations} />}
        {(fromNotes || card.canDispute) && (
          <div className={styles.foot}>
            <span className={styles.footText}>{fromNotes ? "Graded against your notes" : ""}</span>
            {card.canDispute && (
              <button type="button" onClick={() => onDispute(card.id)} className={`${styles.dispute} hov`}>
                <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 21V4h10l-1.5 4L15 12H5" />
                </svg>
                Dispute grade
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
