"use client";

import Link from "next/link";
import Header from "@/components/Header";
import HealthGate from "@/components/HealthGate";
import type { SubjectSummary } from "@/lib/types";
import styles from "./OnboardingShell.module.css";

type Props = {
  subjects: SubjectSummary[];
  eyebrow: string;
  title: string;
  lede: string;
  children: React.ReactNode;
};

/** The onboarding page: header, the learner's subjects, and a column for the form. */
export default function OnboardingShell({ subjects, eyebrow, title, lede, children }: Props) {
  return (
    <div className={styles.page}>
      <Header minimal />
      <HealthGate>
        <div className={styles.body}>
          <aside className={styles.aside}>
            <h2 className={styles.heading}>Your subjects</h2>
            {subjects.length === 0 ? (
              <div className={styles.none}>
                <p className={styles.noneTitle}>No subjects yet</p>
                <p className={styles.noneText}>
                  The first one you set up will appear here, with its memory levels.
                </p>
              </div>
            ) : (
              <ul className={styles.subjects}>
                {subjects.map((s) => (
                  <li key={s.id}>
                    <Link href={`/study/${s.id}`} className={`${styles.subject} hov`}>
                      <span className={styles.subjectName}>{s.name}</span>
                      <span className={styles.subjectMeta}>{s.concept_count} concepts</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </aside>
          <main className={styles.main}>
            <div className={styles.column}>
              <div className={styles.intro}>
                <p className={styles.eyebrow}>{eyebrow}</p>
                <h1 className={styles.title}>{title}</h1>
                {lede && <p className={styles.lede}>{lede}</p>}
              </div>
              {children}
            </div>
          </main>
        </div>
      </HealthGate>
    </div>
  );
}
