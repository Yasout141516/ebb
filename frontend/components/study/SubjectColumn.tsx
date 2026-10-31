"use client";

import Link from "next/link";
import { type RefObject, useEffect, useId, useRef, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import { ThemeToggle } from "@/components/Header";
import { ApiError, getJson } from "@/lib/api";
import { modalKeys, useFocusReturn } from "@/lib/modal";
import { percent } from "@/lib/format";
import type { ConceptState, Concepts, LessonPage, Source, SubjectSummary } from "@/lib/types";
import LessonDialog from "./LessonDialog";
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

/**
 * The concepts among `candidates` (ids) with a lesson learned, asked of the API once each
 * (one studied before lessons has none). `known` are learned already, without asking.
 */
function useLearnedLessons(candidates: string[], known: string[]): Set<string> {
  const [found, setFound] = useState<string[]>([]);
  const asked = useRef(new Set<string>());
  const controller = useRef<AbortController | null>(null);
  const key = candidates.filter((id) => !known.includes(id)).join(" ");
  useEffect(() => {
    controller.current ??= new AbortController();
    const signal = controller.current.signal;
    for (const id of key.split(" ").filter((id) => id && !asked.current.has(id))) {
      asked.current.add(id);
      getJson<LessonPage>(`/concepts/${id}/lesson`, signal)
        .then((page) => {
          if (page.lesson.learned_at !== null) setFound((f) => [...f, id]);
        })
        .catch((error) => {
          // None kept (404): it stays asked. Anything else is asked again with the next list.
          if (!(error instanceof ApiError && error.status === 404)) asked.current.delete(id);
        });
    }
  }, [key]);
  useEffect(() => () => controller.current?.abort(), []);
  return new Set([...found, ...known]);
}

/** A concept's line under its name in the left column. */
function conceptMeta(c: ConceptState, learning: boolean): string {
  if (learning) return "Learning now · not quizzed yet";
  if (c.tide === null) return "Not quizzed yet";
  return `Tide ${percent(c.tide)}%${c.due ? ", due today" : ""}`;
}

type Props = {
  subjectId: string;
  /** Changes after every turn: the due counts are loaded again. */
  updates: number;
  /** The subject's concepts, as the memory panel has them. */
  concepts?: Concepts | null;
  /** The concept in its learn phase, by id. */
  learning?: string | null;
  /** The concepts learned in this tab, by id. */
  learned?: string[];
};

/**
 * The left column: the learner's subjects (switching leaves the session open and
 * resumable) and this subject's sources. At 375 px it opens from the bar's menu button.
 */
export default function SubjectColumn({ subjectId, updates, concepts = null, learning = null, learned = [] }: Props) {
  const subjects = useLoaded<SubjectSummary[]>("/subjects", updates);
  const sources = useLoaded<Source[]>(`/subjects/${subjectId}/sources`, updates);
  const [open, setOpen] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const asideId = useId();
  const current = subjects.value?.find((s) => s.id === subjectId);
  const count = sources.value?.length;
  const listId = useId();
  const list = concepts?.concepts ?? [];
  // Only a concept quizzed at least once can have a lesson learned on another visit.
  const lessons = useLearnedLessons(
    list.filter((c) => c.tide !== null && c.id !== learning).map((c) => c.id),
    learned,
  );
  const [reading, setReading] = useState<ConceptState | null>(null);

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
        {list.length > 0 && (
          <section className={styles.section}>
            <h2 id={listId} className={styles.heading}>
              Concepts
            </h2>
            <ul aria-labelledby={listId} className={styles.concepts}>
              {list.map((c) => (
                <li key={c.id} className={`${styles.concept} ${c.id === learning ? styles.learning : ""}`}>
                  <span id={`${listId}-${c.id}`} className={styles.conceptName}>
                    {c.name}
                  </span>
                  <span className={styles.conceptMeta}>{conceptMeta(c, c.id === learning)}</span>
                  {lessons.has(c.id) && c.id !== learning && (
                    <button
                      type="button"
                      onClick={() => setReading(c)}
                      aria-describedby={`${listId}-${c.id}`}
                      className={`${styles.again} hov`}
                    >
                      Read the lesson again
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
        <SourceList
          subjectId={subjectId}
          sources={sources.value}
          failed={sources.failed}
          onRetry={sources.retry}
        />
      </aside>
      {reading && <LessonDialog conceptId={reading.id} concept={reading.name} onClose={() => setReading(null)} />}
    </>
  );
}
