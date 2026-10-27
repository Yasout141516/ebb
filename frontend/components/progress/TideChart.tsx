"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { dayMonth } from "@/lib/format";
import type { Chart, ChartConcept } from "@/lib/types";
import styles from "./TideChart.module.css";

const DAY = 86_400_000;
const NOT_STUDIED = "Not studied yet. It joins the queue after its first quiz.";
const NEW_USER = "Your tide chart fills in as you review — come back after 3 sessions";

type Point = { t: number; v: number };
type Line = { points: Point[]; reviews: Point[]; ahead: Point[]; start: number; now: number };

const r1 = (n: number) => Math.round(n * 10) / 10;
const percent = (v: number) => Math.round(v * 100);
const times = (n: number) => (n === 1 ? "1 time" : `${n} times`);

/** A concept's measured points, its reviews (each rise; the first tide after none) and the 3 days ahead. */
function lineOf(c: ChartConcept): Line {
  const points: Point[] = [];
  const reviews: Point[] = [];
  let before: number | null | undefined;
  for (const [at, v] of c.past) {
    if (v !== null) {
      const point = { t: Date.parse(at), v };
      points.push(point);
      if (before === null || (before !== undefined && v > before + 1e-6)) reviews.push(point);
    }
    before = v;
  }
  // The API always sends at least "now"; an empty line still gets a 30-day frame.
  const now = c.past.length ? Date.parse(c.past[c.past.length - 1][0]) : Date.now();
  const start = c.past.length ? Date.parse(c.past[0][0]) : now - 30 * DAY;
  const last = points[points.length - 1];
  const ahead = last ? [last, ...c.ahead.map(([at, v]) => ({ t: Date.parse(at), v }))] : [];
  return { points, reviews, ahead, start, now };
}

/** How many reviews raised the concept's tide in the last 30 days. */
export const reviewCount = (c: ChartConcept) => lineOf(c).reviews.length;

/** Round dots as one path, as the prototype draws them. */
const dots = (points: [number, number][], r: number) =>
  points.map(([x, y]) => `M${r1(x - r)} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`).join(" ");

const polyline = (points: Point[], x: (t: number) => number, y: (v: number) => number) =>
  points.map((p) => `${r1(x(p.t))},${r1(y(p.v))}`).join(" ");

function described(c: ChartConcept, line: Line): string {
  if (!c.studied || c.now === null) return `${c.name}: not studied yet.`;
  const inThree = line.ahead.length > 1 ? line.ahead[line.ahead.length - 1].v : null;
  return (
    `${c.name} tide over the last 30 days: reviewed ${times(line.reviews.length)}, now ${percent(c.now)} percent` +
    (inThree === null ? "." : `, about ${percent(inThree)} percent in 3 days without review.`)
  );
}

/** The big chart: 30 days past and 3 ahead across x 40–890, tide 0–100% across y 210–20. */
function BigChart({ c, line }: { c: ChartConcept; line: Line }) {
  const end = line.now + 3 * DAY;
  const x = (t: number) => 40 + ((t - line.start) / (end - line.start)) * 850;
  const y = (v: number) => 210 - v * 190;
  const labels = [0, 7, 14, 21].map((d) => line.start + d * DAY).concat(end);
  const today = r1(x(line.now));
  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>
        {c.name} · now {percent(c.now ?? 0)}%
      </figcaption>
      <svg viewBox="0 0 900 250" width="100%" role="img" aria-label={described(c, line)} className={styles.big}>
        <line x1="40" y1="20" x2="890" y2="20" className={styles.grid} />
        <line x1="40" y1="115" x2="890" y2="115" className={styles.grid} />
        <line x1="40" y1="210" x2="890" y2="210" className={styles.base} />
        <text x="32" y="24" textAnchor="end" className={styles.axis}>
          100%
        </text>
        <text x="32" y="119" textAnchor="end" className={styles.axis}>
          50%
        </text>
        <text x="32" y="214" textAnchor="end" className={styles.axis}>
          0%
        </text>
        {labels.map((t) => (
          <text key={t} x={r1(x(t))} y="234" textAnchor="middle" className={styles.axis}>
            {dayMonth(new Date(t).toISOString())}
          </text>
        ))}
        <line x1={today} y1="14" x2={today} y2="210" className={styles.today} />
        <text x={today} y="10" textAnchor="middle" className={styles.todayLabel}>
          Today
        </text>
        <polyline points={polyline(line.points, x, y)} className={styles.line} />
        <polyline points={polyline(line.ahead, x, y)} className={styles.ahead} />
        <path d={dots(line.reviews.map((p) => [r1(x(p.t)), r1(y(p.v))]), 5)} className={styles.bigDots} />
      </svg>
    </figure>
  );
}

/** A small chart of the 30 days past: 240×64 on a concept button, 320×44 at 375 px. */
function Spark({ line, compact }: { line: Line; compact: boolean }) {
  const [w, h] = compact ? [320, 44] : [240, 64];
  const span = Math.max(line.now - line.start, 1); // a single point sits at the start
  const x = (t: number) => 4 + ((t - line.start) / span) * (w - 8);
  const y = (v: number) => h - 4 - v * (h - 8);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" aria-hidden="true" className={styles.spark}>
      {!compact && <line x1="0" y1="32" x2="240" y2="32" className={styles.sparkMid} />}
      <polyline points={polyline(line.points, x, y)} className={styles.sparkLine} />
      <path d={dots(line.reviews.map((p) => [r1(x(p.t)), r1(y(p.v))]), 3)} className={styles.sparkDots} />
    </svg>
  );
}

/** D5: under 3 days of answers the chart is replaced by one line and one action. */
function NewUser({ subjectId }: { subjectId: string }) {
  return (
    <div className={styles.empty}>
      <span aria-hidden="true" className={styles.emptyGauge} />
      <p className={styles.emptyText}>{NEW_USER}</p>
      <Link href={`/study/${subjectId}`} className={`${styles.emptyAction} pbtn`}>
        Start a quiz
      </Link>
    </div>
  );
}

const byTide = (a: ChartConcept, b: ChartConcept) => (b.now ?? -1) - (a.now ?? -1);

type Props = { chart: Chart; subjectId: string; compact?: boolean };

/**
 * Tide levels, past 30 days: one line per studied concept, rising at each review, the next
 * 3 days without review dashed. Picking a concept enlarges its line. At 375 px, a small
 * chart per concept.
 */
export default function TideChart({ chart, subjectId, compact = false }: Props) {
  const concepts = [...chart.concepts].sort(byTide);
  const studied = concepts.filter((c) => c.studied && c.now !== null);
  // The lowest tide first: the one most in need of a look.
  const [picked, setPicked] = useState<string | null>(studied[studied.length - 1]?.id ?? null);
  const id = useId();
  const selected = studied.find((c) => c.id === picked);

  if (compact) {
    return (
      <section aria-labelledby={id} className={styles.compact}>
        <h2 id={id} className={styles.compactTitle}>
          Tide levels
        </h2>
        {!chart.enough_days ? (
          <NewUser subjectId={subjectId} />
        ) : (
          concepts.map((c) => {
            const line = lineOf(c);
            const on = c.studied && c.now !== null;
            return (
              <div
                key={c.id}
                role="img"
                aria-label={
                  on
                    ? `${c.name}: reviewed ${times(line.reviews.length)} in 30 days, now ${percent(c.now!)} percent`
                    : `${c.name}: not studied yet`
                }
                className={styles.compactCard}
              >
                <span className={styles.compactHead}>
                  <span className={styles.compactName}>{c.name}</span>
                  <span className={styles.compactLevel}>{on ? `${percent(c.now!)}%` : "—"}</span>
                </span>
                {on ? <Spark line={line} compact /> : <span className={styles.compactNever}>{NOT_STUDIED}</span>}
              </div>
            );
          })
        )}
      </section>
    );
  }

  return (
    <section aria-labelledby={id} className={styles.section}>
      <div className={styles.head}>
        <div className={styles.titles}>
          <h2 id={id} className={styles.title}>
            Tide levels, past 30 days
          </h2>
          {chart.enough_days && <p className={styles.lede}>Each rise is a review. Pick a concept to see it larger.</p>}
        </div>
        {chart.enough_days && (
          <p className={styles.legend}>
            <span className={styles.key}>
              <span aria-hidden="true" className={styles.keyLine} />
              Tide
            </span>
            <span className={styles.key}>
              <span aria-hidden="true" className={styles.keyDot} />
              Review
            </span>
            <span className={styles.key}>
              <span aria-hidden="true" className={styles.keyAhead} />
              Next 3 days without review
            </span>
          </p>
        )}
      </div>
      {!chart.enough_days ? (
        <NewUser subjectId={subjectId} />
      ) : (
        <>
          {selected && <BigChart c={selected} line={lineOf(selected)} />}
          <div role="group" aria-label="Choose a concept" className={styles.picker}>
            {concepts.map((c) => {
              const line = lineOf(c);
              const on = c.studied && c.now !== null;
              const pressed = on && c.id === selected?.id;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => on && setPicked(c.id)}
                  aria-pressed={pressed}
                  aria-disabled={on ? undefined : true}
                  aria-label={described(c, line)}
                  className={`${styles.card} ${pressed ? styles.pressed : ""} ${on ? "" : styles.never} mult`}
                >
                  <span className={styles.cardHead}>
                    <span className={styles.cardName}>{c.name}</span>
                    <span className={styles.cardLevel}>{on ? `${percent(c.now!)}%` : "—"}</span>
                  </span>
                  {on ? <Spark line={line} compact={false} /> : <span className={styles.cardNever}>{NOT_STUDIED}</span>}
                </button>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
