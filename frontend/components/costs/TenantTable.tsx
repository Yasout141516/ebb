import { money, tokens } from "@/lib/costFormat";
import type { CostBreakdown } from "@/lib/types";
import shared from "./shared.module.css";

/** Calls and estimated cost per user. */
export default function TenantTable({ tenants }: { tenants: CostBreakdown[] }) {
  return (
    <section aria-label="By tenant" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>By tenant</h2>
      </div>
      <div className={shared.scroll}>
        <table className={shared.table}>
          <thead>
            <tr>
              <th scope="col">Tenant</th>
              <th scope="col" className={shared.n}>Calls</th>
              <th scope="col" className={shared.n}>Est. cost</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => (
              <tr key={t.key}>
                <td>{t.label}</td>
                <td className={shared.n}>{tokens(t.calls)}</td>
                <td className={shared.n}>{money(t.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {tenants.length <= 1 && (
        <p className={shared.note}>One user until sign-in ships in milestone 6. More rows appear then.</p>
      )}
    </section>
  );
}
