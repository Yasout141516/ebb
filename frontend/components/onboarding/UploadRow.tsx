import { megabytes } from "@/lib/format";
import { kindLabel } from "@/lib/notes";
import type { DraftFileReady, FileSaved } from "@/lib/types";
import styles from "./UploadRow.module.css";

export type RowState =
  | { status: "uploading"; progress: number }
  | { status: "reading" }
  | { status: "finding" }
  | { status: "ready"; ready: DraftFileReady }
  | { status: "saved"; saved: FileSaved }
  | { status: "refused"; message: string; retry: boolean }
  | { status: "stored"; detail: string }; // a file the subject already has

export function isWorking(state: RowState): boolean {
  return state.status === "uploading" || state.status === "reading" || state.status === "finding";
}

type Props = {
  filename: string;
  size?: number; // bytes, known for files chosen in this browser
  state: RowState;
  onRemove?: () => void;
  onRetry?: () => void;
};

function meta(size: number | undefined, state: RowState): string {
  const prefix = size === undefined ? "" : `${megabytes(size)} · `;
  switch (state.status) {
    case "uploading":
      return `${prefix}Uploading ${state.progress}%`;
    case "reading":
      return `${prefix}Reading…`;
    case "finding":
      return `${prefix}Finding concepts…`;
    case "ready": {
      const pages = state.ready.page_count === null ? "" : `${state.ready.page_count} pages · `;
      return `${prefix}${pages}${state.ready.passages} passages`;
    }
    case "saved":
      return state.saved.detail;
    case "stored":
      return state.detail;
    case "refused":
      return "";
  }
}

/** What a screen reader hears as the row moves on; the visible line also carries progress. */
function announcement(filename: string, state: RowState): string {
  switch (state.status) {
    case "uploading":
      return `${filename}: uploading`;
    case "reading":
      return `${filename}: reading`;
    case "finding":
      return `${filename}: finding concepts`;
    case "ready":
      return `${filename}: ready, ${state.ready.concepts.length} concepts found`;
    case "saved":
      return `${filename}: saved`;
    default:
      return "";
  }
}

/** What `ebb add` reports: "9 concepts in these notes — 2 already here (…), 7 new". */
function savedReport({ matched, fresh }: FileSaved): string {
  let line = `${matched.length + fresh.length} concepts in these notes — `;
  if (matched.length > 0) line += `${matched.length} already here (${matched.join(", ")}), `;
  return `${line}${fresh.length} new${fresh.length > 0 ? ":" : ""}`;
}

/** A refusal's first sentence goes on the row; any advice after it goes below. */
function splitMessage(message: string): [string, string] {
  const match = message.match(/^(.+?[.!?])\s+(\S[\s\S]*)$/);
  return match ? [match[1], match[2]] : [message, ""];
}

function Cross() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" className={styles.cross}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** One file on its way into a subject: uploading, reading, finding concepts, then done or refused. */
export default function UploadRow({ filename, size, state, onRemove, onRetry }: Props) {
  const remove = onRemove && (
    <button
      type="button"
      onClick={onRemove}
      aria-label={`Remove ${filename}`}
      className={`${styles.remove} hov`}
    >
      <Cross />
    </button>
  );

  if (state.status === "refused") {
    const [headline, advice] = splitMessage(state.message);
    return (
      <div className={styles.refused}>
        <div role="alert" className={styles.alert}>
          <span aria-hidden="true" className={styles.smallBadge}>
            {kindLabel(filename)}
          </span>
          <span className={styles.alertText}>
            <span className={styles.alertName}>{filename}</span>
            <span className={styles.alertMessage}>{headline}</span>
          </span>
          {state.retry && onRetry && (
            <button type="button" onClick={onRetry} className={`${styles.retry} pbtn`}>
              Try again
            </button>
          )}
          {remove}
        </div>
        {advice && <p className={styles.advice}>{advice}</p>}
      </div>
    );
  }

  const working = isWorking(state);
  const progress = state.status === "uploading" ? state.progress : 100;
  return (
    <div className={styles.row}>
      <div className={styles.top}>
        <span aria-hidden="true" className={styles.badge}>
          {kindLabel(filename)}
        </span>
        <div className={styles.text}>
          <span className={styles.filename}>{filename}</span>
          <span className={styles.meta}>{meta(size, state)}</span>
        </div>
        {state.status !== "stored" && (
          <span aria-live="polite" className="sr-only">
            {announcement(filename, state)}
          </span>
        )}
        {(state.status === "ready" || state.status === "saved") && (
          <span className={styles.done}>
            <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" className={styles.check}>
              <path d="m5 12.5 4.5 4.5L19 7.5" />
            </svg>
            {state.status === "ready" ? "Ready" : "Saved"}
          </span>
        )}
        {state.status === "ready" && remove}
      </div>
      {working && (
        <div
          role="progressbar"
          aria-label="Upload progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          className={styles.track}
        >
          <div
            className={`${styles.bar} ${state.status === "uploading" ? "" : styles.working}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      {state.status === "ready" && (
        <div className={styles.concepts}>
          <span className={styles.found}>{state.ready.concepts.length} concepts found:</span>
          {state.ready.concepts.map((name) => {
            // R18: every concept is listed; the ones this file adds are marked new.
            const fresh = state.ready.new.includes(name);
            const marked = fresh && state.ready.new.length < state.ready.concepts.length;
            return (
              <span key={name} className={`${styles.chip} ${fresh ? "" : styles.known}`}>
                {name}
                {marked && <span className="sr-only"> (new)</span>}
              </span>
            );
          })}
        </div>
      )}
      {state.status === "saved" && (
        <div className={styles.concepts}>
          <span className={styles.found}>{savedReport(state.saved)}</span>
          {state.saved.fresh.map((c) => (
            <span key={c.name} className={styles.chip}>
              {c.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
