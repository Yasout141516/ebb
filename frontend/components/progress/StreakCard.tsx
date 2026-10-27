import type { Streak } from "@/lib/types";
import shared from "./shared.module.css";
import styles from "./StreakCard.module.css";

export const days = (n: number) => (n === 1 ? "1 day" : `${n} days`);

/** Each of the 14 days: not studied, studied, or part of the run still going. */
type Mark = "none" | "studied" | "run";

function marks(last14: boolean[]): Mark[] {
  const out = last14.map((on): Mark => (on ? "studied" : "none"));
  // The run ends today, or yesterday while today has no answer yet.
  let i = last14.length - 1;
  if (!last14[i]) i -= 1;
  for (; i >= 0 && last14[i]; i -= 1) out[i] = "run";
  return out;
}

type Props = { streak: Streak; compact?: boolean };

/** Study streak: days in a row with an answer in any subject, and the last two weeks as dots. */
export default function StreakCard({ streak, compact = false }: Props) {
  if (compact) {
    return (
      <div className={shared.tile}>
        <span className={shared.tileLabel}>Streak</span>
        <span className={shared.tileValue}>{days(streak.current)}</span>
        <span className={shared.tileNote}>Best: {streak.best}</span>
      </div>
    );
  }
  const studied = streak.last14.filter(Boolean).length;
  const last = streak.last14.length - 1;
  return (
    <div className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.title}>Study streak</h2>
        <span className={shared.note}>Best: {days(streak.best)}</span>
      </div>
      <p className={shared.value}>{days(streak.current)}</p>
      <div role="img" aria-label={`Studied on ${studied} of the last 14 days`} className={styles.dots}>
        {marks(streak.last14).map((mark, i) => (
          <span key={i} data-day={mark} data-today={i === last || undefined} className={styles.dot} />
        ))}
      </div>
    </div>
  );
}
