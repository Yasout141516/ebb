"use client";

import { Fragment, useState } from "react";
import { duration, modelName, money, tokens } from "@/lib/costFormat";
import type { CostCall } from "@/lib/types";
import styles from "./RecentCalls.module.css";
import shared from "./shared.module.css";

const clock = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });

/** What the status column says about one attempt, in words. */
function status(call: CostCall): string {
  if (call.anomaly) return `Anomaly: ${call.anomaly}`;
  if (call.outcome === "error") return `Failed: ${call.error_kind ?? "error"}`;
  if (call.outcome === "refused") return "Refused";
  if (call.outcome === "invalid") return "Invalid reply";
  return call.backup ? "Fallback result used" : "OK";
}

/** The attempts of each request together, newest request first (the API's order), oldest attempt first within one. */
function grouped(calls: CostCall[]): CostCall[][] {
  const groups = new Map<string, CostCall[]>();
  for (const call of calls) groups.set(call.call_id, [...(groups.get(call.call_id) ?? []), call]);
  return [...groups.values()].map((g) => g.sort((a, b) => a.attempt - b.attempt));
}

function Cells({ call, lead }: { call: CostCall; lead: React.ReactNode }) {
  return (
    <>
      <td>{lead}</td>
      <td>{call.endpoint_label}</td>
      <td>{modelName(call.provider, call.model)}</td>
      <td className={shared.n}>{tokens(call.input_tokens)}</td>
      <td className={shared.n}>{tokens(call.output_tokens)}</td>
      <td className={shared.n}>{duration(call.ttft_ms)}</td>
      <td className={shared.n}>{duration(call.total_ms)}</td>
      <td className={shared.n}>{money(call.cost_usd)}</td>
      <td>{status(call)}</td>
    </>
  );
}

/**
 * The last attempts, newest first. A request that retried or fell back shows its final attempt,
 * with a button that opens the earlier ones beneath it. Anomalies are shaded and named in the status.
 */
export default function RecentCalls({ calls }: { calls: CostCall[] }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((was) => {
      const next = new Set(was);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  return (
    <section aria-label="Recent calls" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>Recent calls</h2>
        <p className={shared.sub}>Shaded rows are anomalies. The status column says so in words.</p>
      </div>
      <div className={shared.scroll}>
        <table className={shared.table}>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Endpoint</th>
              <th scope="col">Model</th>
              <th scope="col" className={shared.n}>In</th>
              <th scope="col" className={shared.n}>Out</th>
              <th scope="col" className={shared.n}>First token</th>
              <th scope="col" className={shared.n}>Total</th>
              <th scope="col" className={shared.n}>Est. cost</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {grouped(calls).map((attempts) => {
              const last = attempts[attempts.length - 1];
              const earlier = attempts.slice(0, -1);
              const expanded = open.has(last.call_id);
              return (
                <Fragment key={last.call_id}>
                  <tr className={last.anomaly ? shared.flag : undefined}>
                    <Cells
                      call={last}
                      lead={
                        <>
                          {clock.format(new Date(last.at))}
                          {earlier.length > 0 && (
                            <button
                              type="button"
                              aria-expanded={expanded}
                              onClick={() => toggle(last.call_id)}
                              className={`${styles.toggle} hov`}
                            >
                              {attempts.length} attempts
                              <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true" className={expanded ? styles.up : styles.chevron}>
                                <path d="m6 9 6 6 6-6" />
                              </svg>
                            </button>
                          )}
                        </>
                      }
                    />
                  </tr>
                  {expanded &&
                    earlier.map((a) => (
                      <tr key={a.id} className={`${styles.earlier} ${a.anomaly ? shared.flag : ""}`}>
                        <Cells call={a} lead={`Attempt ${a.attempt}`} />
                      </tr>
                    ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
