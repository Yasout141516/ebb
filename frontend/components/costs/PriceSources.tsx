"use client";

import { getJson } from "@/lib/api";
import { modelName } from "@/lib/costFormat";
import type { CostPrice } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import shared from "./shared.module.css";

/** Each model's price per million tokens, where it came from and when. */
export default function PriceSources() {
  const { loaded } = useLoad<{ prices: CostPrice[] }>(async (signal) => {
    return { prices: await getJson<CostPrice[]>("/costs/prices", signal) };
  }, "prices");

  return (
    <section aria-label="Where the prices come from" className={shared.card}>
      <div className={shared.head}>
        <h2 className={shared.heading}>Where the prices come from</h2>
        <p className={shared.sub}>
          Dollars per million tokens, as each provider listed them on the date shown. Prices change often, so check the
          provider&apos;s own page before relying on them.
        </p>
      </div>
      {loaded === null && <p className={shared.note}>Loading the prices…</p>}
      {(loaded === "failed" || loaded === "missing") && <p className={shared.note}>Couldn&apos;t load the prices.</p>}
      {loaded !== null && loaded !== "failed" && loaded !== "missing" && (
        <div className={shared.scroll}>
          <table className={shared.table}>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col" className={shared.n}>Input</th>
                <th scope="col" className={shared.n}>Output</th>
                <th scope="col">Free tier</th>
                <th scope="col">Source</th>
                <th scope="col">As of</th>
              </tr>
            </thead>
            <tbody>
              {loaded.prices.map((p) => (
                <tr key={`${p.provider}/${p.model}`}>
                  <td>{modelName(p.provider, p.model)}</td>
                  <td className={shared.n}>${p.input_per_m}</td>
                  <td className={shared.n}>${p.output_per_m}</td>
                  <td>{p.free_tier ? "Yes" : "No"}</td>
                  <td>
                    {/^https?:\/\//.test(p.source) ? (
                      <a href={p.source} target="_blank" rel="noreferrer">
                        {p.source}
                      </a>
                    ) : (
                      p.source
                    )}
                  </td>
                  <td>{p.as_of}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
