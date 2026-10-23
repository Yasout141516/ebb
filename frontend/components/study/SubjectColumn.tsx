"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { ThemeToggle } from "@/components/Header";
import { getJson } from "@/lib/api";
import type { Source, SubjectSummary } from "@/lib/types";
import { Gauge } from "./MemoryPanel";
import SourceList from "./SourceList";
import styles from "./SubjectColumn.module.css";

/** GET `path` into state, again whenever `key` changes; null until it first arrives. */
function useLoaded<T>(path: string, key: unknown): T | null {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getJson<T>(path, controller.signal)
      .then(setValue)
      .catch(() => {
        // keep what is shown; the next update tries again
      });
    return () => controller.abort();
  }, [path, key]);
  return value;
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
  const sources = useLoaded<Source[]>(`/subjects/${subjectId}/sources`, null);
  const [open, setOpen] = useState(false);
  const asideId = useId();
  const current = subjects?.find((s) => s.id === subjectId);
  const count = sources?.length ?? 0;

  return (
    <>
      <div className={styles.bar}>
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
          <span className={styles.barMeta}>{count === 1 ? "1 source" : `${count} sources`}</span>
        </div>
        <ThemeToggle plain />
      </div>
      {open && <div aria-hidden="true" className={styles.scrim} onClick={() => setOpen(false)} />}
      <aside
        id={asideId}
        aria-label="Subjects and sources"
        className={`${styles.left} ${open ? styles.open : ""}`}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        <section className={styles.section}>
          <h2 className={styles.heading}>Subjects</h2>
          {subjects?.map((s) => {
            const here = s.id === subjectId;
            const calm = s.concept_count === 0 ? 0 : (s.concept_count - s.due_count) / s.concept_count;
            return (
              <Link
                key={s.id}
                href={`/study/${s.id}`}
                aria-current={here ? "page" : undefined}
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
        <SourceList subjectId={subjectId} sources={sources} />
      </aside>
    </>
  );
}
