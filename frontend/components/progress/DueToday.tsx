import Link from "next/link";
import { Gauge, NothingDue } from "@/components/study/MemoryPanel";
import { tidePercent } from "@/lib/format";
import type { DueConcept } from "@/lib/types";
import styles from "./DueToday.module.css";
import shared from "./shared.module.css";

type Props = {
  due: DueConcept[];
  /** The subject's earliest next review: what "nothing due" names. */
  nextReview: string | null;
  subjectId: string;
  compact?: boolean;
};

/** The subject's due concepts, lowest tide first, and Review these now (no minutes estimate). */
export default function DueToday({ due, nextReview, subjectId, compact = false }: Props) {
  const sorted = [...due].sort((a, b) => (a.tide ?? 0) - (b.tide ?? 0));
  const calm =
    sorted.length === 0 &&
    (nextReview ? (
      <NothingDue at={nextReview} className={styles.calm} />
    ) : (
      <p className={styles.calm}>Nothing here yet.</p>
    ));
  const review = sorted.length > 0 && (
    <Link href={`/study/${subjectId}`} className={compact ? `${styles.button} pbtn` : styles.link}>
      Review these now
    </Link>
  );

  if (compact) {
    return (
      <section className={styles.section}>
        <h2 className={styles.heading}>Due today · {sorted.length}</h2>
        {calm}
        {sorted.length > 0 && (
          <ul className={styles.plain}>
            {sorted.map((c) => (
              <li key={c.name} className={styles.compactRow}>
                <Gauge tide={c.tide} className={shared.smallGauge} />
                <span className={styles.name}>{c.name}</span>
                <span className={styles.compactLevel}>{tidePercent(c.tide, "—")}</span>
              </li>
            ))}
          </ul>
        )}
        {review}
      </section>
    );
  }
  return (
    <div className={`${shared.card} ${styles.card}`}>
      <h2 className={shared.title}>Due today</h2>
      {calm}
      {sorted.length > 0 && (
        <ul className={styles.list}>
          {sorted.map((c) => (
            <li key={c.name} className={styles.row}>
              <Gauge tide={c.tide} className={shared.rowGauge} />
              <span className={styles.name}>{c.name}</span>
              <span className={styles.level}>{tidePercent(c.tide, "—")}</span>
            </li>
          ))}
        </ul>
      )}
      {review}
    </div>
  );
}
