import { duration, money, percent, tokens } from "@/lib/costFormat";
import type { CostSummary } from "@/lib/types";
import styles from "./SummaryCards.module.css";

function Card({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className={styles.card}>
      <h3 className={styles.label}>{label}</h3>
      <p className={styles.value}>{value}</p>
      {note && <p className={styles.note}>{note}</p>}
    </div>
  );
}

/** "1,310 in", or "input not reported". */
const side = (n: number | null, word: string, unit: string) =>
  n === null ? `${word} not reported` : `${tokens(n)} ${unit}`;

/** The range's totals: cost, tokens, calls, first-token and total times, fallback and retry rates. */
export default function SummaryCards({
  summary: s,
  daily,
}: {
  summary: CostSummary;
  daily: { used: number; limit: number };
}) {
  // A total only when both sides were reported: a missing side is not zero.
  const known = s.input_tokens !== null && s.output_tokens !== null;
  const unpriced = s.unpriced_calls > 0 && !s.all_free_tier;
  return (
    <section aria-label="Summary" className={styles.grid}>
      <Card
        label="Estimated cost on a paid plan"
        value={money(s.cost_usd)}
        note={
          s.all_free_tier
            ? "Real spend: $0.00 on free tiers"
            : unpriced
              ? `${s.unpriced_calls} ${s.unpriced_calls === 1 ? "call has" : "calls have"} no known price`
              : undefined
        }
      />
      <Card
        label="Tokens"
        value={known ? tokens((s.input_tokens ?? 0) + (s.output_tokens ?? 0)) : "not reported"}
        note={`${side(s.input_tokens, "input", "in")} · ${side(s.output_tokens, "output", "out")}`}
      />
      <Card label="Model calls" value={tokens(s.calls)} note={`${s.retried} retried · ${s.fell_back} fell back`} />
      <Card
        label="First token, p50 / p95"
        value={`${duration(s.ttft_p50_ms)} / ${duration(s.ttft_p95_ms)}`}
        note="Streaming calls only"
      />
      <Card
        label="Total time, p50 / p95"
        value={`${duration(s.total_p50_ms)} / ${duration(s.total_p95_ms)}`}
        note="Until the last token"
      />
      <Card
        label="Fell back to a backup"
        value={percent(s.fallback_rate)}
        note={`${s.fell_back} of ${tokens(s.calls)} calls`}
      />
      <Card
        label="Retried after a bad reply"
        value={percent(s.retry_rate)}
        note={`${s.retried} of ${tokens(s.calls)} calls`}
      />
      <Card
        label="Daily call limit used"
        value={`${daily.used} / ${daily.limit}`}
        note="Resets at midnight, your time zone"
      />
    </section>
  );
}
