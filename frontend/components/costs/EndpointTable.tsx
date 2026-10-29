import { money, tokens } from "@/lib/costFormat";
import type { CostBreakdown } from "@/lib/types";
import shared from "./shared.module.css";

/** Cost over calls; a cost with an unpriced call in it is already null. */
const perCall = (e: CostBreakdown) => (e.cost_usd === null || e.calls === 0 ? null : String(Number(e.cost_usd) / e.calls));

/** Calls, tokens and estimated cost for each endpoint (each kind of model call). */
export default function EndpointTable({ endpoints }: { endpoints: CostBreakdown[] }) {
  return (
    <section aria-label="By endpoint" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>By endpoint</h2>
      </div>
      <div className={shared.scroll}>
        <table className={shared.table}>
          <thead>
            <tr>
              <th scope="col">Endpoint</th>
              <th scope="col" className={shared.n}>Calls</th>
              <th scope="col" className={shared.n}>Tokens in</th>
              <th scope="col" className={shared.n}>Tokens out</th>
              <th scope="col" className={shared.n}>Est. cost</th>
              <th scope="col" className={shared.n}>Per call</th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map((e) => (
              <tr key={e.key}>
                <td>{e.label}</td>
                <td className={shared.n}>{tokens(e.calls)}</td>
                <td className={shared.n}>{tokens(e.input_tokens)}</td>
                <td className={shared.n}>{tokens(e.output_tokens)}</td>
                <td className={shared.n}>{money(e.cost_usd)}</td>
                <td className={shared.n}>{money(perCall(e))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
