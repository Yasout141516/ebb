// How the Costs page writes its numbers. A null is an unknown, never a zero.

/** An estimated cost from the API's decimal string: "$0.00042"; unknown is "price unknown". */
export function money(usd: string | null): string {
  if (usd === null) return "price unknown";
  const n = Number(usd);
  if (n >= 1 || n === 0) return `$${n.toFixed(2)}`;
  // Two significant digits, so a tiny amount never shows as $0.00; trailing zeros go, cents stay.
  const decimals = Math.min(8, Math.ceil(-Math.log10(n)) + 1);
  return `$${n.toFixed(decimals).replace(/(\.\d\d)0+$/, "$1").replace(/(\.\d*[1-9])0+$/, "$1")}`;
}

/** A token count with thousands separators; unknown is "not reported". */
export function tokens(n: number | null): string {
  return n === null ? "not reported" : n.toLocaleString("en-US");
}

/** A time: "840 ms" under a second, else "2.9 s"; unknown is a dash. */
export function duration(ms: number | null): string {
  if (ms === null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** A share (0–1) as "8.3%"; unknown is a dash. */
export function percent(share: number | null): string {
  return share === null ? "—" : `${(share * 100).toFixed(1)}%`;
}

const PROVIDERS: Record<string, string> = { groq: "Groq", mistral: "Mistral", openrouter: "OpenRouter" };

/** "Groq, gpt-oss-120b": the provider's name, then the model without its vendor prefix. */
export function modelName(provider: string, model: string): string {
  return `${PROVIDERS[provider] ?? provider}, ${model.split("/").pop()}`;
}

/** modelName for a breakdown's "provider/model" key. */
export function keyedModelName(key: string): string {
  const slash = key.indexOf("/");
  return modelName(key.slice(0, slash), key.slice(slash + 1));
}
