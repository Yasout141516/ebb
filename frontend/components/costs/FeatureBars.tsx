import { money } from "@/lib/costFormat";
import type { CostBreakdown } from "@/lib/types";
import styles from "./FeatureBars.module.css";
import shared from "./shared.module.css";

/** Estimated cost per feature as plain bars; the longest is the dearest feature. */
export default function FeatureBars({ features }: { features: CostBreakdown[] }) {
  const used = features.filter((f) => f.calls > 0);
  const most = Math.max(0, ...used.map((f) => Number(f.cost_usd ?? 0)));
  const width = (cost: string | null) =>
    cost === null || most === 0 ? 0 : `${Math.max(2, (Number(cost) / most) * 100)}%`;
  return (
    <section aria-label="Estimated cost by feature" className={`${shared.card} ${shared.pad}`}>
      <h2 className={shared.heading}>Estimated cost by feature</h2>
      <ul className={styles.list}>
        {used.map((f) => (
          <li key={f.key}>
            <div className={styles.row}>
              <span>{f.label}</span>
              <span className={styles.amount}>{money(f.cost_usd)}</span>
            </div>
            <div className={styles.track} aria-hidden="true">
              <div className={styles.fill} style={{ width: width(f.cost_usd) }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
