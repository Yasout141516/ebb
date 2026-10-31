import { duration, modelName, shareText } from "@/lib/costFormat";
import type { CostRole } from "@/lib/types";
import shared from "./shared.module.css";

const ROLES: Record<string, string> = {
  teach: "Teaching and lessons",
  quiz: "Quizzing and grading",
  eval_writer: "Eval writer",
};

/** Each role's main and backup model, the share of its recent attempts that failed, and its p95 total time. */
export default function GatewayRoles({ roles }: { roles: CostRole[] }) {
  return (
    <section aria-label="Gateway: who handles what" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>Gateway: who handles what</h2>
        <p className={shared.sub}>Each role has one main model and, usually, a backup. The gateway switches on errors.</p>
      </div>
      <div className={shared.scroll}>
        <table className={shared.table}>
          <thead>
            <tr>
              <th scope="col">Role</th>
              <th scope="col">Main model</th>
              <th scope="col">Backup</th>
              <th scope="col">Recent health</th>
              <th scope="col" className={shared.n}>
                p95 total
              </th>
            </tr>
          </thead>
          <tbody>
            {roles.map((r) => (
              <tr key={r.role}>
                <td>{ROLES[r.role] ?? r.role}</td>
                <td>{modelName(r.main.provider, r.main.model)}</td>
                <td>{r.backup ? modelName(r.backup.provider, r.backup.model) : "none"}</td>
                <td>{r.attempts === 0 ? "Idle" : r.failed_share === null ? "—" : `${shareText(r.failed_share)} failed`}</td>
                <td className={shared.n}>{duration(r.p95_total_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
