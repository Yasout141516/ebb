"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import { ApiError, del, messageOf, postJson, uploadFile } from "@/lib/api";
import { MAX_FILES, uploadRefusal } from "@/lib/notes";
import { CUT_OFF } from "@/lib/sse";
import type {
  CommittedDraft,
  CreatedDraft,
  DraftFileEvent,
  Source,
  SourceFileEvent,
  SubjectSummary,
} from "@/lib/types";
import DropZone from "./DropZone";
import shared from "./shared.module.css";
import StartButton from "./StartButton";
import styles from "./UploadPanel.module.css";
import UploadRow, { isWorking, type RowState } from "./UploadRow";

type Props =
  // A new subject: files gather in a draft, saved together by Start studying.
  | { mode: "draft"; subjects: SubjectSummary[] }
  // Notes for an existing subject: one file at a time, each saved when its concepts are found.
  | { mode: "add"; subject: SubjectSummary; sources: Source[] };

type Row = { key: number; file: File; state: RowState };

const existsCopy = (name: string) =>
  `${name} already exists — add notes to it from its Sources list.`;

export default function UploadPanel(props: Props) {
  const router = useRouter();
  const draftMode = props.mode === "draft";
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null); // the API's refusal
  const [draftId, setDraftId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [expired, setExpired] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const nextKey = useRef(0);
  // Each upload waits for the one before it to start, so the API reads them in order.
  const queue = useRef<Promise<void>>(Promise.resolve());
  // Bumped by Start over: uploads from the discarded draft no longer touch the page.
  const generation = useRef(0);
  // The uploads in flight, aborted by Start over and when the page goes away.
  const uploads = useRef(new Set<AbortController>());

  useEffect(() => {
    const inFlight = uploads.current;
    return () => inFlight.forEach((c) => c.abort());
  }, []);

  const trimmed = name.trim();
  const taken = draftMode
    ? props.subjects.find((s) => s.name.toLowerCase() === trimmed.toLowerCase())
    : undefined;
  const nameMessage = nameError ?? (taken ? existsCopy(taken.name) : null);
  const locked = draftId !== null || creating;
  const stored = props.mode === "add" ? props.sources : [];
  const counted = stored.length + rows.filter((r) => r.state.status !== "refused").length;
  const full = counted >= MAX_FILES;
  const working = rows.some((r) => isWorking(r.state));
  const zoneDisabled =
    full ||
    expired !== null ||
    (draftMode ? trimmed === "" || nameMessage !== null || creating : working);
  const canStart =
    draftId !== null &&
    rows.some((r) => r.state.status === "ready") &&
    !working &&
    expired === null;

  function uploadPath(id: string | null): string {
    return props.mode === "add" ? `/subjects/${props.subject.id}/sources` : `/drafts/${id}/files`;
  }

  function setRow(key: number, state: RowState) {
    setRows((current) => current.map((r) => (r.key === key ? { ...r, state } : r)));
  }

  function enqueue(row: Row, path: string) {
    const gen = generation.current;
    let started: () => void = () => {};
    const turn = new Promise<void>((resolve) => (started = resolve));
    const previous = queue.current;
    queue.current = turn;
    void previous.then(() => send(row, path, gen, started)).finally(started);
  }

  async function send(row: Row, path: string, gen: number, started: () => void) {
    if (generation.current !== gen) return; // queued before Start over
    const update = (state: RowState) => {
      if (generation.current === gen) setRow(row.key, state);
    };
    // Progress can trail the first event: it never moves a row back to uploading.
    const progressed = (progress: number) => {
      if (generation.current !== gen) return;
      setRows((current) =>
        current.map((r) =>
          r.key === row.key && r.state.status === "uploading"
            ? { ...r, state: { status: "uploading", progress } }
            : r,
        ),
      );
    };
    const controller = new AbortController();
    uploads.current.add(controller);
    let ended = false;
    try {
      await uploadFile(
        path,
        row.file,
        progressed,
        ({ event, data }) => {
          if (event !== "file") return;
          started();
          const file = data as DraftFileEvent | SourceFileEvent;
          if (file.status === "ready") update({ status: "ready", ready: file });
          else if (file.status === "saved") update({ status: "saved", saved: file });
          else if (file.status === "refused") {
            update({ status: "refused", message: file.message, retry: file.retry });
          } else update({ status: file.status });
          ended = file.status !== "reading" && file.status !== "finding";
        },
        controller.signal,
      );
      if (!ended) update({ status: "refused", message: CUT_OFF, retry: true });
    } catch (e) {
      if (controller.signal.aborted) return; // Start over, or the page went away
      if (e instanceof ApiError && e.status === 410) {
        // The draft is gone: the card says so, and every row goes with Start over.
        if (generation.current === gen) {
          setExpired(e.message);
          setRows((current) => current.filter((r) => r.key !== row.key));
        }
        return;
      }
      const retry = !(e instanceof ApiError) || e.status === 0 || e.status === 200;
      update({ status: "refused", message: messageOf(e), retry });
    } finally {
      uploads.current.delete(controller);
    }
  }

  async function createDraft(): Promise<string | null> {
    setCreating(true);
    try {
      const draft = await postJson<CreatedDraft>("/drafts", { name: trimmed });
      setDraftId(draft.id);
      return draft.id;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNameError(e.message);
      else setError(messageOf(e));
      return null;
    } finally {
      setCreating(false);
    }
  }

  async function addFiles(files: File[]) {
    setError(null);
    const subjectName = props.mode === "add" ? props.subject.name : trimmed;
    let free = MAX_FILES - counted;
    const added: Row[] = files.map((file) => {
      const refusal =
        uploadRefusal(file) ??
        (free <= 0 ? `${subjectName} can have at most ${MAX_FILES} files.` : null);
      if (refusal === null) free -= 1;
      const state: RowState = refusal
        ? { status: "refused", message: refusal, retry: false }
        : { status: "uploading", progress: 0 };
      return { key: nextKey.current++, file, state };
    });
    const toSend = added.filter((r) => r.state.status === "uploading");
    let id = draftId;
    if (draftMode && toSend.length > 0 && id === null) {
      id = await createDraft();
      if (id === null) return;
    }
    setRows((current) => [...current, ...added]);
    for (const row of toSend) enqueue(row, uploadPath(id));
  }

  function retry(row: Row) {
    const again = { ...row, state: { status: "uploading", progress: 0 } as RowState };
    setRow(row.key, again.state);
    enqueue(again, uploadPath(draftId));
  }

  async function remove(row: Row) {
    const drop = () => setRows((current) => current.filter((r) => r.key !== row.key));
    if (row.state.status !== "ready") return drop();
    try {
      await del(`/drafts/${draftId}/files/${row.state.ready.file_id}`);
      drop();
    } catch (e) {
      if (e instanceof ApiError && e.status === 410) setExpired(e.message);
      else if (e instanceof ApiError && e.status === 404) drop();
      else setError(messageOf(e));
    }
  }

  async function commit() {
    setCommitting(true);
    setError(null);
    try {
      const done = await postJson<CommittedDraft>(`/drafts/${draftId}/commit`);
      router.push(`/study/${done.subject_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNameError(e.message);
      else if (e instanceof ApiError && e.status === 410) setExpired(e.message);
      else setError(messageOf(e));
      setCommitting(false);
    }
  }

  /**
   * Discard the draft and free the name: cancel its uploads, and take its ready files back
   * out of it (best effort; the API drops an unused draft within the hour anyway).
   */
  function startOver() {
    generation.current += 1;
    uploads.current.forEach((c) => c.abort());
    uploads.current.clear();
    queue.current = Promise.resolve();
    for (const row of rows) {
      if (row.state.status === "ready" && draftId !== null) {
        del(`/drafts/${draftId}/files/${row.state.ready.file_id}`).catch(() => {});
      }
    }
    setDraftId(null);
    setRows([]);
    setExpired(null);
    setError(null);
    setNameError(null);
  }

  const empty = rows.length === 0 && stored.length === 0;

  return (
    <>
      <section className={styles.upload}>
        {draftMode && (
          <div className={styles.name}>
            <label htmlFor="subj" className={styles.nameLabel}>
              Subject name
            </label>
            <div className={styles.nameRow}>
              <input
                id="subj"
                type="text"
                value={name}
                readOnly={locked}
                aria-invalid={nameMessage !== null || undefined}
                onChange={(e) => {
                  setName(e.target.value);
                  setNameError(null);
                }}
                className={styles.nameInput}
              />
              {draftId !== null && (
                <button type="button" onClick={startOver} className={`${styles.startOver} hov`}>
                  Start over
                </button>
              )}
            </div>
            {nameMessage && (
              <p role="alert" className={shared.fieldError}>
                {nameMessage}
              </p>
            )}
          </div>
        )}

        <DropZone
          title={full ? `${MAX_FILES} of ${MAX_FILES} files` : "Drop lecture notes here"}
          disabled={zoneDisabled}
          multiple={draftMode}
          onFiles={addFiles}
        />

        <div role="note" className={styles.note}>
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className={styles.noteIcon}>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v5M12 8h.01" />
          </svg>
          <span>
            <strong className={styles.noteStrong}>Don&apos;t upload sensitive documents.</strong>{" "}
            Files are processed by an AI model to write questions and cite answers.
          </span>
        </div>

        <div className={styles.sources}>
          <h2 className={styles.heading}>Sources</h2>
          {empty && draftMode && (
            <p className={styles.empty}>
              No uploads yet. Choose a file, or skip and type a topic instead.
            </p>
          )}
          {empty && !draftMode && (
            <div className={styles.noSources}>
              <p className={styles.noSourcesTitle}>No sources yet</p>
              <p className={styles.noSourcesText}>
                Grades will be marked “General knowledge” until you add notes. PDF, TXT or MD, up
                to 20 MB.
              </p>
            </div>
          )}
          {stored.map((s) => (
            <UploadRow key={s.id} filename={s.filename} state={{ status: "stored", detail: s.detail }} />
          ))}
          {rows.map((row) => (
            <UploadRow
              key={row.key}
              filename={row.file.name}
              size={row.file.size}
              state={row.state}
              onRemove={
                row.state.status === "ready" || row.state.status === "refused"
                  ? () => remove(row)
                  : undefined
              }
              onRetry={() => retry(row)}
            />
          ))}
        </div>
      </section>

      {expired && (
        <ErrorCard title={expired} action={{ label: "Start over", onClick: startOver }} />
      )}
      {error && <ErrorCard title={error} />}

      <div className={shared.footer}>
        {props.mode === "draft" ? (
          <StartButton disabled={!canStart || committing} onClick={commit} />
        ) : (
          <StartButton
            disabled={false}
            onClick={() => router.push(`/study/${props.subject.id}`)}
          />
        )}
      </div>
    </>
  );
}
