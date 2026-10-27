import type { CSSProperties } from "react";
import { dayMonth } from "@/lib/format";
import type { Average } from "@/lib/types";
import styles from "./AverageTide.module.css";
import shared from "./shared.module.css";

/** "+9 points since 23 Sep", "−3 points since …" (a true minus sign); `short`: "+9 since 23 Sep". */
export function changeText(change: number, since: string, short = false): string {
  const points = Math.round(change * 100);
  const sign = points > 0 ? "+" : points < 0 ? "−" : "";
  const unit = short ? "" : Math.abs(points) === 1 ? " point" : " points";
  return `${sign}${Math.abs(points)}${unit} since ${dayMonth(since)}`;
}

function names(list: string[]): string {
  if (list.length > 3) return `${list[0]}, ${list[1]} and ${list.length - 2} more`;
  return list.length === 1 ? list[0] : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** Which concepts the average counts, and which wait for their first review. */
export function footnote(counted: number, notCounted: string[]): string {
  const across = counted === 0 ? "" : `Across ${counted} studied ${counted === 1 ? "concept" : "concepts"}.`;
  const waiting =
    notCounted.length === 0
      ? ""
      : notCounted.length === 1
        ? `${notCounted[0]} isn't counted until its first review.`
        : `${names(notCounted)} aren't counted until their first review.`;
  return [across, waiting].filter(Boolean).join(" ");
}

const trend = (change: number) => {
  const points = Math.round(change * 100);
  return points > 0 ? styles.up : points < 0 ? styles.down : styles.flat;
};

type Props = { average: Average; compact?: boolean };

/** The mean tide of the studied concepts, and how far it moved in 30 days. */
export default function AverageTide({ average, compact = false }: Props) {
  const level = average.now === null ? "—" : `${Math.round(average.now * 100)}%`;
  if (compact) {
    return (
      <div className={shared.tile}>
        <span className={shared.tileLabel}>Average tide</span>
        <span className={shared.tileValue}>{level}</span>
        {average.change !== null && (
          <span className={`${styles.shortChange} ${trend(average.change)}`}>
            {changeText(average.change, average.since, true)}
          </span>
        )}
      </div>
    );
  }
  const note = footnote(average.counted, average.not_counted);
  return (
    <div className={shared.card}>
      <h2 className={shared.title}>Average tide</h2>
      <div className={styles.level}>
        <span aria-hidden="true" className={styles.gauge} style={{ "--tide": average.now ?? 0 } as CSSProperties}>
          <span className={styles.fill} />
        </span>
        <div className={styles.numbers}>
          <p className={shared.value}>{level}</p>
          {average.change !== null && (
            <p className={`${styles.change} ${trend(average.change)}`}>{changeText(average.change, average.since)}</p>
          )}
        </div>
      </div>
      {note && <p className={styles.footnote}>{note}</p>}
    </div>
  );
}
