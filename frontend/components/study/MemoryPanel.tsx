"use client";

import Link from "next/link";
import { type CSSProperties, type ReactNode, useEffect, useId, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import { getJson } from "@/lib/api";
import { calendarDays, INTERVAL_DAYS, percent, stageDots, weekday } from "@/lib/format";
import type { ConceptState, Concepts } from "@/lib/types";
import styles from "./MemoryPanel.module.css";

const TIDE_TIP = "Tide: the chance you'd recall it right now. It ebbs every day and rises when you review.";
const STAGE_TIP = "Stage: how far along its reviews are. Each right answer moves it up and widens the gap.";
const LOAD_FAILED = "Couldn't load your memory levels.";
const LEARNING = "learning · not quizzed yet";

/** The gap before the next review if the next answer at this stage is right. */
export function nextGap(stage: number): number {
  return INTERVAL_DAYS[Math.min(stage + 1, 5)];
}

/** How long a due concept has been due, in calendar days of the browser's zone. */
export function dueText(nextReviewAt: string | null, now: Date): string {
  if (nextReviewAt === null) return "due today";
  const days = calendarDays(new Date(nextReviewAt), now);
  if (days <= 0) return "due today";
  return days === 1 ? "due yesterday" : `due ${days} days ago`;
}

/** What the gauge's dashed marker shows, as text (screen readers can't see the gauge). */
function AheadText({ c }: { c: ConceptState }) {
  if (c.tide === null || c.projected_tide === null) return null;
  return <span className="sr-only">, about {percent(c.projected_tide)}% in 3 days without review</span>;
}

/** A tide value: "71%", or "—" with its words for a concept never studied. */
function TideValue({ tide, className }: { tide: number | null; className: string }) {
  return (
    <span className={className}>
      <span className="sr-only">Tide</span>{" "}
      {tide === null ? (
        <>
          <span aria-hidden="true">—</span>
          <span className="sr-only">not measured yet,</span>
        </>
      ) : (
        `${percent(tide)}%`
      )}
    </span>
  );
}

type TipProps = {
  tip: string;
  className: string;
  /** Line the tooltip up with the trigger's end edge rather than its start. */
  end?: boolean;
  children: ReactNode;
};

/**
 * A one-line tooltip (D7) on `children`: a plain button that shows `tip` while hovered or
 * focused, linked to it with aria-describedby; Escape hides it.
 */
function Tip({ tip, className, end = false, children }: TipProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={styles.tipBox} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        aria-describedby={id}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key !== "Escape" || !open) return;
          e.stopPropagation(); // the tooltip, not the sheet around it
          setOpen(false);
        }}
        className={`${styles.tipTrigger} ${className}`}
      >
        {children}
      </button>
      <span role="tooltip" id={id} hidden={!open} className={`${styles.tip} ${end ? styles.tipEnd : ""}`}>
        {tip}
      </span>
    </span>
  );
}

const byTide = (a: ConceptState, b: ConceptState) => (b.tide ?? -1) - (a.tide ?? -1);
const dueFirst = (concepts: ConceptState[]) =>
  concepts.filter((c) => c.due).sort((a, b) => (a.tide ?? 0) - (b.tide ?? 0));

type GaugeProps = {
  tide: number | null;
  /** Where the tide will be in 3 days without review: the dashed marker. */
  ahead?: number | null;
  /** The gauge's frame, from the prototype's sizes. */
  className: string;
  mini?: boolean;
};

/** A tide gauge: a rounded frame filled to the tide. */
export function Gauge({ tide, ahead = null, className, mini = false }: GaugeProps) {
  const style = { "--tide": tide ?? 0, "--ahead": ahead ?? 0 } as CSSProperties;
  return (
    <span aria-hidden="true" className={`${styles.gauge} ${className}`} style={style} data-mini={mini || undefined}>
      <span className={mini ? styles.miniFill : styles.fill} />
      {tide !== null && ahead !== null && <span className={styles.ahead} data-projection />}
    </span>
  );
}

/** A concept's stage as the prototype's small dots, from `stageDots`. */
function Dots({ stage }: { stage: number }) {
  return (
    <span aria-hidden="true" className={styles.dots}>
      {[...stageDots(stage)].map((dot, i) => (
        <span key={i} data-dot={dot} className={dot === "●" ? styles.dotOn : styles.dot} />
      ))}
    </span>
  );
}

/** A subject's concepts with their tides, loaded again whenever `updates` changes. */
export function useConcepts(subjectId: string, updates: number) {
  const [concepts, setConcepts] = useState<Concepts | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getJson<Concepts>(`/subjects/${subjectId}/concepts`, controller.signal)
      .then((loaded) => {
        setConcepts(loaded);
        setFailed(false);
      })
      .catch((error) => {
        if (error?.name !== "AbortError") setFailed(true);
      });
    return () => controller.abort();
  }, [subjectId, updates, attempt]);

  return { concepts, failed, retry: () => setAttempt((n) => n + 1) };
}

/** Nothing due: when the next review is (D5), without D5's "Quiz ahead anyway"; `className` styles its box. */
export function NothingDue({ at, className = styles.calm }: { at: string; className?: string }) {
  return <p className={className}>Tide&apos;s high. Next review {weekday(at, undefined, true)}.</p>;
}

type Props = {
  concepts: Concepts | null;
  /** The concept in its learn phase, by id. */
  learning?: string | null;
  failed?: boolean;
  onRetry?: () => void;
  now?: Date;
};

/** The right column: what's due, and every concept's tide, band and stage. */
export default function MemoryPanel({ concepts, learning = null, failed = false, onRetry, now = new Date() }: Props) {
  const due = concepts ? dueFirst(concepts.concepts) : [];
  const id = useId();
  return (
    <>
      <div className={styles.head}>
        <h2 className={styles.title}>Memory</h2>
        <p className={styles.lede}>How high each idea&apos;s tide is right now</p>
      </div>
      {concepts === null && failed && (
        <ErrorCard title={LOAD_FAILED} action={onRetry && { label: "Retry", onClick: onRetry }} />
      )}
      {concepts === null && !failed && (
        <div aria-busy="true" aria-label="Loading memory levels" className={styles.loading}>
          <div className={`skel ${styles.skelHeading}`} />
          <div className={`skel ${styles.skelDue}`} />
          <div className={`skel ${styles.skelDue}`} />
          <div className={`skel ${styles.skelHeading} ${styles.skelGap}`} />
          <div className={`skel ${styles.skelRow}`} />
          <div className={`skel ${styles.skelRow}`} />
          <div className={`skel ${styles.skelRow}`} />
          <div className={`skel ${styles.skelRow}`} />
        </div>
      )}
      {concepts && (
        <>
          {(due.length > 0 || concepts.next_review_at) && (
            <section aria-labelledby={`${id}-due`} className={styles.dueSection}>
              <h3 id={`${id}-due`} className={styles.label}>
                Due for review · {due.length}
              </h3>
              {due.length > 0 ? (
                <ul className={styles.dueList}>
                  {due.map((c) => (
                    <li key={c.id} className={styles.dueRow}>
                      <Gauge tide={c.tide} className={styles.dueGauge} />
                      <span className={styles.dueText}>
                        <span className={styles.dueName}>{c.name}</span>
                        <span className={styles.dueMeta}>
                          Tide {percent(c.tide)}% · {dueText(c.next_review_at, now)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <NothingDue at={concepts.next_review_at!} />
              )}
            </section>
          )}
          <section aria-labelledby={`${id}-all`} className={styles.allSection}>
            <h3 id={`${id}-all`} className={`${styles.label} ${styles.allLabel}`}>
              All concepts
            </h3>
            <ul className={styles.list}>
              {[...concepts.concepts].sort(byTide).map((c) => (
                <li key={c.id} className={styles.row}>
                  <Gauge tide={c.tide} ahead={c.projected_tide} className={styles.rowGauge} />
                  <span className={styles.middle}>
                    <span className={styles.name}>{c.name}</span>
                    <Tip tip={STAGE_TIP} className={styles.stage}>
                      <Dots stage={c.stage} />
                      <span className={styles.stageText}>
                        {c.id === learning
                          ? LEARNING
                          : c.tide === null
                            ? "No reviews yet"
                            : `level ${c.stage} · next gap ${nextGap(c.stage)} days`}
                      </span>
                    </Tip>
                  </span>
                  <Tip tip={TIDE_TIP} className={styles.level} end>
                    <TideValue tide={c.tide} className={styles.value} />{" "}
                    <span className={styles.band}>{c.band}</span>
                    <AheadText c={c} />
                  </Tip>
                </li>
              ))}
            </ul>
            <p className={styles.legend}>
              <span aria-hidden="true" className={styles.legendMark} />
              Where the tide will be in 3 days without review
            </p>
          </section>
        </>
      )}
    </>
  );
}

/** The bottom sheet's peek (375 px): a small gauge per concept and the due count. */
export function MemoryPeek({ concepts }: { concepts: Concepts | null }) {
  const list = concepts?.concepts ?? [];
  return (
    <>
      <span aria-hidden="true" className={styles.minis}>
        {list.map((c) => (
          <Gauge key={c.id} tide={c.tide} className={styles.miniGauge} mini />
        ))}
      </span>
      <span className={styles.peekDue}>{list.filter((c) => c.due).length} due</span>
    </>
  );
}

/** The bottom sheet's body (375 px), as tpl-WorkspaceMobile lays it out; it links to `subjectId`'s dashboard. */
export function MemorySheetBody({
  concepts,
  learning = null,
  subjectId,
  failed = false,
  onRetry,
  now = new Date(),
}: Props & { subjectId: string }) {
  if (concepts === null) {
    return failed ? (
      <ErrorCard title={LOAD_FAILED} action={onRetry && { label: "Retry", onClick: onRetry }} />
    ) : (
      <p aria-busy="true" className={styles.sheetLoading}>
        Loading memory levels…
      </p>
    );
  }
  const due = dueFirst(concepts.concepts);
  return (
    <>
      {(due.length > 0 || concepts.next_review_at) && (
        <section className={styles.sheetDue}>
          <h3 className={styles.label}>Due for review · {due.length}</h3>
          {due.length === 0 && <NothingDue at={concepts.next_review_at!} />}
          <ul className={styles.sheetDueList}>
            {due.map((c) => (
              <li key={c.id} className={styles.sheetDueRow}>
                <Gauge tide={c.tide} className={styles.sheetDueGauge} />
                <span className={styles.sheetName}>{c.name}</span>
                <TideValue tide={c.tide} className={styles.sheetValue} />
                <span className="sr-only">, {dueText(c.next_review_at, now)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <ul className={styles.sheetList}>
        {[...concepts.concepts].sort(byTide).map((c) => (
          <li key={c.id} className={styles.sheetRow}>
            <Gauge tide={c.tide} ahead={c.projected_tide} className={styles.sheetGauge} />
            <span className={styles.sheetName}>{c.name}</span>
            <span className={styles.sheetLevel}>
              <TideValue tide={c.tide} className={styles.sheetValue} />{" "}
              <span className={styles.band}>{c.id === learning ? LEARNING : c.band}</span>
              <AheadText c={c} />
            </span>
          </li>
        ))}
      </ul>
      <Link href={`/progress/${subjectId}`} className={styles.sheetLink}>
        Open full progress
      </Link>
    </>
  );
}
