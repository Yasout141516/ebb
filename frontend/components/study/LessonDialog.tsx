"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ErrorCard from "@/components/ErrorCard";
import { getJson, messageOf } from "@/lib/api";
import { modalKeys, useFocusReturn } from "@/lib/modal";
import type { LessonPage } from "@/lib/types";
import LearnExchange from "./LearnExchange";
import LearnLesson from "./LearnLesson";
import styles from "./LessonDialog.module.css";

type Props = {
  conceptId: string;
  concept: string;
  onClose: () => void;
};

/** Read the lesson again: a learned concept's kept lesson and its questions, read-only. */
export default function LessonDialog({ conceptId, concept, onClose }: Props) {
  const [page, setPage] = useState<LessonPage | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const id = useId();
  useFocusReturn(close);

  useEffect(() => {
    const controller = new AbortController();
    getJson<LessonPage>(`/concepts/${conceptId}/lesson`, controller.signal)
      .then((loaded) => {
        setPage(loaded);
        setFailed(null);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailed(messageOf(error));
      });
    return () => controller.abort();
  }, [conceptId, attempt]);

  return createPortal(
    <div className={styles.scrim}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onKeyDown={(e) => {
          e.stopPropagation(); // React bubbles through the portal: not the drawer's keys too
          modalKeys(e, box.current, onClose);
        }}
        className={styles.dialog}
      >
        <div className={styles.head}>
          <h2 id={`${id}-title`} className={styles.title}>
            Lesson: {concept}
          </h2>
          <button ref={close} type="button" onClick={onClose} className={`${styles.close} hov`}>
            Close
          </button>
        </div>
        {page === null && failed === null && (
          <p aria-busy="true" className={styles.loading}>
            Loading the lesson…
          </p>
        )}
        {page === null && failed !== null && (
          <ErrorCard title={failed} action={{ label: "Retry", onClick: () => setAttempt((n) => n + 1) }} />
        )}
        {page && (
          <div className={styles.body}>
            <LearnLesson label="Lesson" lesson={page.lesson} hasNotes={page.has_notes} />
            {page.exchanges.map((e) => (
              <LearnExchange key={e.position} question={e.question} exchange={e} hasNotes={page.has_notes} />
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
