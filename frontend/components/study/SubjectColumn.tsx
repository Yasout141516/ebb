"use client";

import Link from "next/link";
import { type RefObject, useEffect, useId, useRef, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import { ThemeToggle } from "@/components/Header";
import { getJson } from "@/lib/api";
import type { Source, SubjectSummary } from "@/lib/types";
import { modalKeys, useFocusReturn } from "./DisputeDialog";
import { Gauge } from "./MemoryPanel";
import SourceList from "./SourceList";
import styles from "./SubjectColumn.module.css";

type Loaded<T> = { value: T | null; failed: boolean; retry: () => void };

/**
 * GET `path` into state, again whenever `key` changes. `value` stays null until it first
 * arrives; a later failure keeps what is shown, and `failed` says the last load failed.
 */
function useLoaded<T>(path: string, key: unknown): Loaded<T> {
  const [value, setValue] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getJson<T>(path, controller.signal)
      .then((loaded) => {
        setValue(loaded);
        setFailed(false);
      })
      .catch((error) => {
        if (error?.name !== "AbortError") setFailed(true);
      });
    return () => controller.abort();
  }, [path, key, attempt]);
  return { value, failed, retry: () => setAttempt((n) => n + 1) };
}

/**
 * The open drawer (375 px) as a modal: focus moves into it and goes back to the menu button
 * when it closes, and the rest of the workspace (the column's siblings but the bar and the
 * scrim, which closes it) is inert.
 */
function DrawerOpen({ drawer, bar }: { drawer: RefObject<HTMLElement | null>; bar: RefObject<HTMLElement | null> }) {
  useFocusReturn(drawer);
  useEffect(() => {
    const parent = drawer.current?.parentElement;
    if (!parent) return;
    const behind = [...parent.children].filter(
      (el) => el !== drawer.current && el !== bar.current && !el.hasAttribute("data-scrim"),
    );
    for (const el of behind) el.setAttribute("inert", "");
    return () => {
      for (const el of behind) el.removeAttribute("inert");
    };
  }, [drawer, bar]);
  return null;
}

type Props = {
  subjectId: string;
  /** Changes after every turn: the due counts are loaded again. */
  updates: number;
};

/**
 * The left column: the learner's subjects (switching leaves the session open and
 * resumable) and this subject's sources. At 375 px it opens from the bar's menu button.
 */
export default function SubjectColumn({ subjectId, updates }: Props) {
  const subjects = useLoaded<SubjectSummary[]>("/subjects", updates);
  const sources = useLoaded<Source[]>(`/subjects/${subjectId}/sources`, updates);
  const [open, setOpen] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const asideId = useId();
  const current = subjects.value?.find((s) => s.id === subjectId);
  const count = sources.value?.length;

  return (
    <>
      <div ref={bar} className={styles.bar}>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-label="Subjects and sources"
          aria-expanded={open}
          aria-controls={asideId}
          className={`${styles.menu} hov`}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" className={styles.icon}>
            <path d="M4 7h16M4 12h16M4 17h10" />
          </svg>
        </button>
        <div className={styles.barText}>
          <span className={styles.barName}>{current?.name ?? ""}</span>
          {count !== undefined && (
            <span className={styles.barMeta}>{count === 1 ? "1 source" : `${count} sources`}</span>
          )}
        </div>
        <ThemeToggle plain />
      </div>
      {open && <div aria-hidden="true" data-scrim className={styles.scrim} onClick={() => setOpen(false)} />}
      <aside
        ref={drawer}
        id={asideId}
        aria-label="Subjects and sources"
        role={open ? "dialog" : undefined}
        aria-modal={open ? true : undefined}
        tabIndex={open ? -1 : undefined}
        className={`${styles.left} ${open ? styles.open : ""}`}
        onKeyDown={open ? (e) => modalKeys(e, drawer.current, () => setOpen(false)) : undefined}
      >
        {open && <DrawerOpen drawer={drawer} bar={bar} />}
        <section className={styles.section}>
          <h2 className={styles.heading}>Subjects</h2>
          {subjects.value === null && subjects.failed && (
            <ErrorCard
              title="Couldn't load your subjects."
              action={{ label: "Retry", onClick: subjects.retry }}
            />
          )}
          {subjects.value?.map((s) => {
            const here = s.id === subjectId;
            const calm = s.concept_count === 0 ? 0 : (s.concept_count - s.due_count) / s.concept_count;
            return (
              <Link
                key={s.id}
                href={`/study/${s.id}`}
                aria-current={here ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={`${styles.subject} ${here ? styles.current : "hov"}`}
              >
                <Gauge tide={calm} className={styles.gauge} />
                <span className={styles.subjectText}>
                  <span className={styles.subjectName}>{s.name}</span>
                  <span className={styles.subjectMeta}>
                    {s.concept_count} concepts · {s.due_count} due today
                  </span>
                </span>
              </Link>
            );
          })}
          <Link href="/new" className={`${styles.add} hov`}>
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className={styles.plus}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New subject
          </Link>
        </section>
        <SourceList
          subjectId={subjectId}
          sources={sources.value}
          failed={sources.failed}
          onRetry={sources.retry}
        />
      </aside>
    </>
  );
}
