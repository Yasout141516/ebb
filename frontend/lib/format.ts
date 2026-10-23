/** A memory level as a percentage; a concept not yet studied is "new". */
export function tidePercent(t: number | null): string {
  return t === null ? "new" : `${Math.round(t * 100)}%`;
}

/** A stage out of five, as filled and empty dots. */
export function stageDots(stage: number): string {
  const filled = Math.max(0, Math.min(5, stage));
  return "●".repeat(filled) + "○".repeat(5 - filled);
}

/** The short weekday name of an instant, in `tz` (the browser's zone by default). */
export function weekday(iso: string, tz?: string): string {
  return new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: tz }).format(new Date(iso));
}
