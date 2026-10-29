import { keyedModelName } from "@/lib/costFormat";
import type { CostBreakdown } from "@/lib/types";
import styles from "./Filters.module.css";

export type Range = "24h" | "7d" | "30d";
export type FilterValues = { range: Range; tenant: string; endpoint: string; model: string };

const RANGES: { value: Range; label: string }[] = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

type Props = {
  values: FilterValues;
  /** What the current range has: tenants, endpoints and models, each as a breakdown's key and label. */
  tenants: CostBreakdown[];
  endpoints: CostBreakdown[];
  models: CostBreakdown[];
  onChange: (values: FilterValues) => void;
};

function Select({
  id,
  label,
  value,
  onChange,
  children,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={styles.select}>
        {children}
      </select>
    </div>
  );
}

/** The first row of each value: two providers can serve the same model id, and the API filters on the id. */
const unique = (rows: { value: string; label: string }[]) =>
  rows.filter((r, i) => rows.findIndex((o) => o.value === r.value) === i);

/** A model breakdown's key is "provider/model"; the API filters on the model alone. */
const modelId = (key: string) => key.slice(key.indexOf("/") + 1);

/** Range, tenant, endpoint and model: each change refetches the page's numbers. */
export default function Filters({ values, tenants, endpoints, models, onChange }: Props) {
  const set = (patch: Partial<FilterValues>) => onChange({ ...values, ...patch });
  // The chosen value stays an option even when the narrowed data no longer lists it.
  const withChosen = (rows: { value: string; label: string }[], chosen: string) =>
    chosen === "" || rows.some((r) => r.value === chosen) ? rows : [...rows, { value: chosen, label: chosen }];
  const options = (rows: { value: string; label: string }[], chosen: string, all: string) => (
    <>
      <option value="">{all}</option>
      {withChosen(unique(rows), chosen).map((r) => (
        <option key={r.value} value={r.value}>
          {r.label}
        </option>
      ))}
    </>
  );

  return (
    <section aria-label="Filters" className={styles.filters}>
      <Select id="costs-range" label="Range" value={values.range} onChange={(v) => set({ range: v as Range })}>
        {RANGES.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </Select>
      <Select id="costs-tenant" label="Tenant" value={values.tenant} onChange={(tenant) => set({ tenant })}>
        {options(tenants.map((t) => ({ value: t.key, label: t.label })), values.tenant, "All tenants")}
      </Select>
      <Select id="costs-endpoint" label="Endpoint" value={values.endpoint} onChange={(endpoint) => set({ endpoint })}>
        {options(endpoints.map((e) => ({ value: e.key, label: e.label })), values.endpoint, "All endpoints")}
      </Select>
      <Select id="costs-model" label="Model" value={values.model} onChange={(model) => set({ model })}>
        {options(models.map((m) => ({ value: modelId(m.key), label: keyedModelName(m.key) })), values.model, "All models")}
      </Select>
    </section>
  );
}
