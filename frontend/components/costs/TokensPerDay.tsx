import { tokens } from "@/lib/costFormat";
import { dayMonth } from "@/lib/format";
import type { CostAnomaly, CostDay } from "@/lib/types";
import shared from "./shared.module.css";
import styles from "./TokensPerDay.module.css";

const HEIGHT = 150; // px of the tallest column

/** Tokens per day as plain columns; a day the rules flagged is marked in words as well as colour. */
export default function TokensPerDay({ days, anomalies }: { days: CostDay[]; anomalies: CostAnomaly[] }) {
  const flagged = new Set(anomalies.filter((a) => a.kind === "day").map((a) => a.day));
  const most = Math.max(0, ...days.map((d) => d.tokens ?? 0));
  const label = days
    .map((d) => `${dayMonth(d.day)} ${tokens(d.tokens)}${flagged.has(d.day) ? " (flagged as an anomaly)" : ""}`)
    .join(", ");
  return (
    <section aria-label="Tokens per day" className={`${shared.card} ${shared.pad}`}>
      <div className={styles.titles}>
        <h2 className={shared.heading}>Tokens per day</h2>
        {flagged.size > 0 && <p className={shared.sub}>Flagged days used more than twice a usual day&apos;s tokens.</p>}
      </div>
      <div role="img" aria-label={`Tokens per day: ${label}`} className={styles.chart}>
        {days.map((d) => (
          <div key={d.day} className={styles.col}>
            <span className={`${styles.value} ${flagged.has(d.day) ? styles.flag : ""}`}>{tokens(d.tokens)}</span>
            <div
              className={`${styles.bar} ${flagged.has(d.day) ? styles.barFlag : ""}`}
              style={{ height: most === 0 ? 0 : Math.max(2, ((d.tokens ?? 0) / most) * HEIGHT) }}
            />
          </div>
        ))}
      </div>
      <div className={styles.days} aria-hidden="true">
        {days.map((d) => (
          <span key={d.day} className={`${styles.day} ${flagged.has(d.day) ? styles.flag : ""}`}>
            {dayMonth(d.day)}
            {flagged.has(d.day) && <span className={styles.mark}>flagged</span>}
          </span>
        ))}
      </div>
    </section>
  );
}
