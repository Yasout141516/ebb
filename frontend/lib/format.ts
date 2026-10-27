/** A memory level (0–1) as a whole percentage number; null counts as 0. */
export function percent(t: number | null): number {
  return Math.round((t ?? 0) * 100);
}

/** A memory level as a percentage; a concept not yet studied is `empty` ("new"). */
export function tidePercent(t: number | null, empty = "new"): string {
  return t === null ? empty : `${percent(t)}%`;
}

/** The review gap in days before a concept comes back, by its stage (backend mastery.INTERVAL_DAYS). */
export const INTERVAL_DAYS = [0, 1, 3, 7, 16, 35];

/** Whole calendar days from `from` to `to` (negative when `to` is earlier), in the browser's zone. */
export function calendarDays(from: Date, to: Date): number {
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((midnight(to) - midnight(from)) / 86_400_000);
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * A day and month as the dashboard writes them ("23 Sep"; Intl's en-GB says "Sept"). A
 * calendar date ("2026-09-23") is shown as it is; an instant in `tz` (the browser's zone by default).
 */
export function dayMonth(iso: string, tz?: string): string {
  const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (date) return `${Number(date[3])} ${MONTHS[Number(date[2]) - 1]}`;
  const parts = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "numeric", timeZone: tz }).formatToParts(
    new Date(iso),
  );
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return `${part("day")} ${MONTHS[part("month") - 1]}`;
}

/** A file size in megabytes with one decimal, as the API writes it ("26.4 MB"). */
export function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
