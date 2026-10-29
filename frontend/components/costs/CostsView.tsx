"use client";

import { useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import { getJson } from "@/lib/api";
import type { CostCall, CostSummaryPage } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import AnomalyBanner from "./AnomalyBanner";
import EndpointTable from "./EndpointTable";
import FeatureBars from "./FeatureBars";
import Filters, { type FilterValues } from "./Filters";
import GatewayRoles from "./GatewayRoles";
import ModelSpeed from "./ModelSpeed";
import PriceSources from "./PriceSources";
import RecentCalls from "./RecentCalls";
import SummaryCards from "./SummaryCards";
import TenantTable from "./TenantTable";
import TokensPerDay from "./TokensPerDay";
import styles from "./CostsView.module.css";

type Loaded = { page: CostSummaryPage; calls: CostCall[] };

const LIMIT = 50;

/** The query the summary and the call list share; a filter left on "all" is left out. */
function query(f: FilterValues): string {
  const params = new URLSearchParams({ range: f.range });
  for (const name of ["tenant", "endpoint", "model"] as const) {
    if (f[name] !== "") params.set(name, f[name]);
  }
  return params.toString();
}

/** The dev Costs page under the header: its filters and every section (tpl "Costs and gateway"). */
export default function CostsView() {
  const [filters, setFilters] = useState<FilterValues>({ range: "7d", tenant: "", endpoint: "", model: "" });
  const q = query(filters);
  const { loaded, retry } = useLoad<Loaded>(async (signal) => {
    const [page, calls] = await Promise.all([
      getJson<CostSummaryPage>(`/costs/summary?${q}`, signal),
      getJson<CostCall[]>(`/costs/calls?${q}&limit=${LIMIT}`, signal),
    ]);
    return { page, calls };
  }, q);

  const data = loaded === null || loaded === "missing" || loaded === "failed" ? null : loaded;
  const page = data?.page;
  return (
    <main className={styles.main}>
      <div className={styles.inner}>
        <div className={styles.titles}>
          <p className={styles.eyebrow}>Every model call · dev only</p>
          <h1 className={styles.title}>Costs and gateway</h1>
          <p className={styles.intro}>
            What each call cost on a paid plan, how fast it was, and which model handled it. This page is hidden before
            sign-in ships.
          </p>
        </div>

        <Filters
          values={filters}
          tenants={page?.by_tenant ?? []}
          endpoints={page?.by_endpoint ?? []}
          models={page?.by_model ?? []}
          onChange={setFilters}
        />

        {loaded === null && (
          <div aria-busy="true" aria-label="Loading the costs" className={styles.loading}>
            <div className={`skel ${styles.skelCards}`} />
            <div className={`skel ${styles.skelTable}`} />
          </div>
        )}
        {loaded === "missing" && <ErrorCard title="Not found." />}
        {loaded === "failed" && (
          <ErrorCard title="Couldn't load the costs." action={{ label: "Retry", onClick: retry }} />
        )}

        {data && page && page.summary.calls === 0 && (
          <p className={styles.empty}>No model calls yet in this range.</p>
        )}
        {data && page && page.summary.calls > 0 && (
          <>
            <AnomalyBanner anomalies={page.anomalies} />
            <SummaryCards summary={page.summary} />
            <div className={styles.pair}>
              <FeatureBars features={page.by_feature} />
              <TokensPerDay days={page.per_day} anomalies={page.anomalies} />
            </div>
            <GatewayRoles roles={page.roles} />
            <ModelSpeed models={page.by_model} />
            <div className={styles.pair}>
              <div className={styles.wide}>
                <EndpointTable endpoints={page.by_endpoint} />
              </div>
              <div className={styles.narrow}>
                <TenantTable tenants={page.by_tenant} />
              </div>
            </div>
            <RecentCalls calls={data.calls} />
            <PriceSources />
          </>
        )}
      </div>
    </main>
  );
}
