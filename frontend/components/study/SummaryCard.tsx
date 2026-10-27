import { day, stageDots, tidePercent } from "@/lib/format";
import type { Finished } from "@/lib/types";
import styles from "./SummaryCard.module.css";

type Props = { finished: Finished; timeZone?: string };

/** The end of a session: each concept studied, and when the next review is. */
export default function SummaryCard({ finished, timeZone }: Props) {
  const { results } = finished;
  const caughtUp =
    finished.next_review_at === null
      ? "You're all caught up."
      : `You're all caught up — next review ${day(finished.next_review_at, timeZone)}.`;
  return (
    <article aria-label="Session summary" className={styles.card}>
      <span className={styles.label}>Session summary</span>
      {results.length === 0 ? (
        <p className={styles.line}>No answers this session. Your memory levels are unchanged.</p>
      ) : (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col" className={styles.th}>Concept</th>
                <th scope="col" className={styles.th}>Answers</th>
                <th scope="col" className={styles.th}>Tide</th>
                <th scope="col" className={styles.th}>Stage</th>
                <th scope="col" className={styles.th}>Next review</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.concept_id}>
                  <th scope="row" className={styles.concept}>{r.concept}</th>
                  <td className={styles.td}>
                    {`${r.tally.correct}✓ ${r.tally.partial}◐ ${r.tally.incorrect}✗`}
                  </td>
                  <td className={`${styles.td} ${styles.mono}`}>
                    {`${tidePercent(r.tide_before)} → ${tidePercent(r.tide_after)}`}
                  </td>
                  <td className={`${styles.td} ${styles.dots}`}>
                    {`${stageDots(r.stage_before)} → ${stageDots(r.stage_after)}`}
                  </td>
                  <td className={`${styles.td} ${styles.next}`}>{day(r.next_review_at, timeZone)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {finished.next_review_line && <p className={styles.line}>{finished.next_review_line}</p>}
      {finished.caught_up && <p className={styles.line}>{caughtUp}</p>}
    </article>
  );
}
