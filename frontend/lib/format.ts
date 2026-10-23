/** A memory level as a percentage; a concept not yet studied is "new". */
export function tidePercent(t: number | null): string {
  return t === null ? "new" : `${Math.round(t * 100)}%`;
}

/** A stage out of five, as filled and empty dots. */
export function stageDots(stage: number): string {
  const filled = Math.max(0, Math.min(5, stage));
  return "●".repeat(filled) + "○".repeat(5 - filled);
}

/** The weekday of an instant, "Thu" (or "Thursday" when `long`), in `tz` (the browser's zone by default). */
export function weekday(iso: string, tz?: string, long = false): string {
  return new Intl.DateTimeFormat("en-US", { weekday: long ? "long" : "short", timeZone: tz }).format(new Date(iso));
}

/** A day as the CLI writes it ("Thu 30 Oct"), in `tz` (the browser's zone by default). */
export function day(iso: string, tz?: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    timeZone: tz,
  }).formatToParts(new Date(iso));
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("weekday")} ${part("day")} ${part("month")}`;
}

/** A time of day on a 24-hour clock ("9:41", "14:05"). */
export function clockTime(moment: Date, tz?: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "numeric",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: tz,
  })
    .format(moment)
    .replace(/^0(\d)/, "$1");
}

/** How long ago `iso` was, worded as the CLI's resume question does. */
export function formatAgo(iso: string, now: Date): string {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "a moment ago";
  if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
}

/** A file size in megabytes with one decimal, as the API writes it ("26.4 MB"). */
export function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
