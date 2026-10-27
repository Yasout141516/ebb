"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import Header, { ThemeToggle } from "@/components/Header";
import HealthGate from "@/components/HealthGate";
import AverageTide from "@/components/progress/AverageTide";
import DueToday from "@/components/progress/DueToday";
import StageColumns from "@/components/progress/StageColumns";
import StreakCard from "@/components/progress/StreakCard";
import SubjectPicker from "@/components/progress/SubjectPicker";
import TideChart from "@/components/progress/TideChart";
import { getJson } from "@/lib/api";
import type { Progress, SubjectSummary } from "@/lib/types";
import styles from "./page.module.css";

type Loaded = { subjects: SubjectSummary[]; progress: Progress };

/** The subject's earliest next review, for the "nothing due" line. */
function nextReview(progress: Progress): string | null {
  const times = progress.stages.flatMap((s) => s.concepts.map((c) => c.next_review_at)).filter((t) => t !== null);
  return times.length === 0 ? null : times.reduce((a, b) => (Date.parse(a) <= Date.parse(b) ? a : b));
}

/** Everything under the title, once the subject's numbers are in (tpl-Dashboard; tpl-DashboardMobile at 375 px). */
function Dashboard({ subjectId }: { subjectId: string }) {
  const [loaded, setLoaded] = useState<Loaded | "missing" | "failed" | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getJson<SubjectSummary[]>("/subjects", controller.signal),
      getJson<Progress>(`/subjects/${subjectId}/progress`, controller.signal),
    ])
      .then(([subjects, progress]) => setLoaded({ subjects, progress }))
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setLoaded(error?.status === 404 || error?.status === 422 ? "missing" : "failed");
      });
    return () => controller.abort();
  }, [subjectId, attempt]);

  if (loaded === null || loaded === "missing" || loaded === "failed") {
    return (
      <main className={styles.main}>
        <div className={styles.inner}>
          {loaded === null && (
            <div aria-busy="true" aria-label="Loading your progress" className={styles.loading}>
              <div className={`skel ${styles.skelTitle}`} />
              <div className={styles.summary}>
                <div className={`skel ${styles.skelCard}`} />
                <div className={`skel ${styles.skelCard}`} />
                <div className={`skel ${styles.skelCard}`} />
              </div>
              <div className={`skel ${styles.skelChart}`} />
            </div>
          )}
          {loaded === "missing" && <ErrorCard title="Not found." />}
          {loaded === "failed" && (
            <ErrorCard
              title="Couldn't load your progress."
              action={{
                label: "Retry",
                onClick: () => {
                  setLoaded(null);
                  setAttempt((n) => n + 1);
                },
              }}
            />
          )}
        </div>
      </main>
    );
  }

  const { subjects, progress } = loaded;
  const tides = new Map(progress.chart.concepts.map((c) => [c.name, c.now]));
  const next = nextReview(progress);
  return (
    <main className={styles.main}>
      <div className={styles.inner}>
        <div className={styles.top}>
          <div className={styles.titles}>
            <p className={styles.eyebrow}>
              <SubjectPicker subjects={subjects} current={subjectId} /> · last 30 days
            </p>
            <h1 className={styles.title}>Progress</h1>
          </div>
          <Link href={`/study/${subjectId}`} className={`${styles.start} pbtn`}>
            Start today&apos;s review
          </Link>
        </div>

        {/* Wide: one row of three cards. 375 px: two tiles, then Due today on its own. */}
        <section aria-label="Summary" className={`${styles.summary} ${styles.wide}`}>
          <StreakCard streak={progress.streak} />
          <DueToday due={progress.due} nextReview={next} subjectId={subjectId} />
          <AverageTide average={progress.average} />
        </section>
        <div className={`${styles.tiles} ${styles.narrow}`}>
          <StreakCard streak={progress.streak} compact />
          <AverageTide average={progress.average} compact />
        </div>
        <div className={styles.narrow}>
          <DueToday due={progress.due} nextReview={next} subjectId={subjectId} compact />
        </div>

        <div className={styles.wide}>
          <TideChart chart={progress.chart} subjectId={subjectId} />
        </div>
        <div className={styles.narrow}>
          <TideChart chart={progress.chart} subjectId={subjectId} compact />
        </div>

        <div className={styles.wide}>
          <StageColumns stages={progress.stages} tides={tides} />
        </div>
        <div className={styles.narrow}>
          <StageColumns stages={progress.stages} tides={tides} compact />
        </div>
      </div>
    </main>
  );
}

/** A subject's progress dashboard. */
export default function ProgressSubject() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className={styles.page}>
      <Header />
      {/* 375 px: tpl-DashboardMobile's bar, back to Study and the theme toggle. */}
      <div className={styles.bar}>
        <Link href={`/study/${id}`} className={styles.back}>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className={styles.backIcon}>
            <path d="M19 12H5M11 6l-6 6 6 6" />
          </svg>
          Study
        </Link>
        <ThemeToggle plain />
      </div>
      <HealthGate>
        <Dashboard key={id} subjectId={id} />
      </HealthGate>
    </div>
  );
}
