import { useId } from "react";
import { Gauge } from "@/components/study/MemoryPanel";
import { calendarDays, INTERVAL_DAYS } from "@/lib/format";
import type { Stage } from "@/lib/types";
import shared from "./shared.module.css";
import styles from "./StageColumns.module.css";

const plural = (n: number) => (n === 1 ? "1 day" : `${n} days`);

/** When a concept comes back, in calendar days of the browser's zone; `due` from today on. */
export function nextText(nextReviewAt: string | null, now: Date): { text: string; due: boolean } {
  if (nextReviewAt === null) return { text: "Due today", due: true };
  const days = calendarDays(now, new Date(nextReviewAt));
  if (days < 0) return { text: `Overdue ${plural(-days)}`, due: true };
  if (days === 0) return { text: "Due today", due: true };
  return { text: days === 1 ? "Tomorrow" : `Next in ${days} days`, due: false };
}

type Column = {
  key: string;
  title: string;
  sub: string;
  items: { name: string; next: { text: string; due: boolean } }[];
};

type Props = {
  stages: Stage[];
  /** Concepts with no review yet: the prototype's sixth column, "Not started". */
  notStarted: string[];
  /** Each concept's tide now, by name, for its gauge. */
  tides: Map<string, number | null>;
  compact?: boolean;
  now?: Date;
};

const EMPTY = "Nothing here yet.";

/** Review stages 1–5 and the concepts not started yet: what sits in each, and when it comes back. */
export default function StageColumns({ stages, notStarted, tides, compact = false, now = new Date() }: Props) {
  const id = useId();
  const columns: Column[] = [
    ...stages.map((st) => ({
      key: String(st.stage),
      title: `Stage ${st.stage}`,
      sub: `back after ${plural(INTERVAL_DAYS[st.stage])}`,
      items: st.concepts.map((c) => ({ name: c.name, next: nextText(c.next_review_at, now) })),
    })),
    {
      key: "0",
      title: "Not started",
      sub: "no reviews yet",
      items: notStarted.map((name) => ({ name, next: { text: "Not started", due: false } })),
    },
  ];
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
        {columns.map((col) => {
          const titleId = `${id}-${col.key}`;
          if (compact) {
            return (
              <div key={col.key} role="group" aria-labelledby={titleId} className={styles.row}>
                <span className={styles.rowHead}>
                  <span id={titleId} className={styles.rowTitle}>
                    {col.title}
                  </span>
                  <span className={styles.rowSub}>{col.sub}</span>
                </span>
                <span className={styles.chips}>
                  {col.items.map((c) => (
                    <span key={c.name} className={styles.chip}>
                      {c.name}
                    </span>
                  ))}
                  {col.items.length === 0 && <span className={styles.empty}>{EMPTY}</span>}
                </span>
              </div>
            );
          }
          return (
            <div key={col.key} role="group" aria-labelledby={titleId} className={styles.column}>
              <div className={styles.columnHead}>
                <h3 id={titleId} className={styles.columnTitle}>
                  {col.title}
                </h3>
                <span className={styles.columnSub}>{col.sub}</span>
              </div>
              {col.items.map((c) => (
                <div key={c.name} className={styles.item}>
                  <Gauge tide={tides.get(c.name) ?? null} className={shared.smallGauge} />
                  <span className={styles.itemText}>
                    <span className={styles.itemName}>{c.name}</span>
                    <span className={c.next.due ? styles.due : styles.next}>{c.next.text}</span>
                  </span>
                </div>
              ))}
              {col.items.length === 0 && <p className={styles.empty}>{EMPTY}</p>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
