import { duration, keyedModelName, tokens } from "@/lib/costFormat";
import type { CostBreakdown } from "@/lib/types";
import shared from "./shared.module.css";

/** Calls, first-token and total times, output speed and fallbacks for each model used. */
export default function ModelSpeed({ models }: { models: CostBreakdown[] }) {
  return (
    <section aria-label="Speed and reliability by model" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>Speed and reliability by model</h2>
      </div>
      <div className={shared.scroll}>
        <table className={shared.table}>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col" className={shared.n}>Calls</th>
              <th scope="col" className={shared.n}>First token p50</th>
              <th scope="col" className={shared.n}>First token p95</th>
              <th scope="col" className={shared.n}>Total p50</th>
              <th scope="col" className={shared.n}>Total p95</th>
              <th scope="col" className={shared.n}>Tokens per second</th>
              <th scope="col" className={shared.n}>Fallbacks</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.key}>
                <td>{keyedModelName(m.key)}</td>
                <td className={shared.n}>{tokens(m.calls)}</td>
                <td className={shared.n}>{duration(m.ttft_p50_ms)}</td>
                <td className={shared.n}>{duration(m.ttft_p95_ms)}</td>
                <td className={shared.n}>{duration(m.total_p50_ms)}</td>
                <td className={shared.n}>{duration(m.total_p95_ms)}</td>
                <td className={shared.n}>
                  {m.output_tokens_per_second === null ? "—" : Math.round(m.output_tokens_per_second)}
                </td>
                <td className={shared.n}>{m.fallbacks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
