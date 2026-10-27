import { useId } from "react";
import { Gauge } from "@/components/study/MemoryPanel";
import type { Stage } from "@/lib/types";
import shared from "./shared.module.css";
import styles from "./StageColumns.module.css";

// The review gap for each stage, in days (backend mastery.INTERVAL_DAYS).
const GAP = [0, 1, 3, 7, 16, 35];
const plural = (n: number) => (n === 1 ? "1 day" : `${n} days`);

/** When a concept comes back, in calendar days of the browser's zone; `due` from today on. */
export function nextText(nextReviewAt: string | null, now: Date): { text: string; due: boolean } {
  if (nextReviewAt === null) return { text: "Due today", due: true };
  const at = new Date(nextReviewAt);
  const days = Math.round(
    (new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime() -
      new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) /
      86_400_000,
  );
  if (days < 0) return { text: `Overdue ${plural(-days)}`, due: true };
  if (days === 0) return { text: "Due today", due: true };
  return { text: days === 1 ? "Tomorrow" : `Next in ${days} days`, due: false };
}

type Props = {
  stages: Stage[];
  /** Each concept's tide now, by name, for its gauge. */
  tides: Map<string, number | null>;
  compact?: boolean;
  now?: Date;
};

const EMPTY = "Nothing here yet.";

/** Review stages 1–5: what sits in each, and when it comes back. */
export default function StageColumns({ stages, tides, compact = false, now = new Date() }: Props) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={styles.section}>
      {compact ? (
        <h2 id={id} className={styles.compactTitle}>
          Review stages
        </h2>
      ) : (
        <div className={styles.head}>
          <h2 id={id} className={styles.title}>
            Review stages
          </h2>
          <p className={styles.lede}>
            A correct review moves a concept up one stage and stretches the gap before it comes back. A miss drops it to
            stage 1.
          </p>
        </div>
      )}
      <div className={compact ? styles.rows : styles.columns}>
        {stages.map((st) => {
          const title = `Stage ${st.stage}`;
          const sub = `back after ${plural(GAP[st.stage])}`;
          if (compact) {
            return (
              <div key={st.stage} className={styles.row}>
                <span className={styles.rowHead}>
                  <span className={styles.rowTitle}>{title}</span>
                  <span className={styles.rowSub}>{sub}</span>
                </span>
                <span className={styles.chips}>
                  {st.concepts.map((c) => (
                    <span key={c.name} className={styles.chip}>
                      {c.name}
                    </span>
                  ))}
                  {st.concepts.length === 0 && <span className={styles.empty}>{EMPTY}</span>}
                </span>
              </div>
            );
          }
          return (
            <div key={st.stage} className={styles.column}>
              <div className={styles.columnHead}>
                <h3 className={styles.columnTitle}>{title}</h3>
                <span className={styles.columnSub}>{sub}</span>
              </div>
              {st.concepts.map((c) => {
                const next = nextText(c.next_review_at, now);
                return (
                  <div key={c.name} className={styles.item}>
                    <Gauge tide={tides.get(c.name) ?? null} className={shared.smallGauge} />
                    <span className={styles.itemText}>
                      <span className={styles.itemName}>{c.name}</span>
                      <span className={next.due ? styles.due : styles.next}>{next.text}</span>
                    </span>
                  </div>
                );
              })}
              {st.concepts.length === 0 && <p className={styles.empty}>{EMPTY}</p>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
