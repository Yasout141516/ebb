import type { CostAnomaly } from "@/lib/types";
import styles from "./AnomalyBanner.module.css";

/** One line per anomaly, in the API's words; nothing at all when there are none. */
export default function AnomalyBanner({ anomalies }: { anomalies: CostAnomaly[] }) {
  if (anomalies.length === 0) return null;
  const n = anomalies.length;
  return (
    <section aria-label="Anomalies" className={styles.banner}>
      <h2 className={styles.title}>
        {n} {n === 1 ? "anomaly" : "anomalies"} in this range
      </h2>
      {anomalies.map((a, i) => (
        <p key={i} className={styles.line}>
          {a.message}
        </p>
      ))}
      <p className={styles.note}>
        An anomaly is a value far outside that endpoint&apos;s own recent normal. A rule in code decides it, not a model.
      </p>
    </section>
  );
}
