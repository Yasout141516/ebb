"use client";

import { useEffect, useState } from "react";
import OnboardingShell from "@/components/onboarding/OnboardingShell";
import TopicForm from "@/components/onboarding/TopicForm";
import UploadPanel from "@/components/onboarding/UploadPanel";
import { getJson } from "@/lib/api";
import type { SubjectSummary } from "@/lib/types";
import styles from "./page.module.css";

type Tab = "upload" | "topic";

/** Onboarding: a new subject from uploaded notes (the default) or from a typed topic. */
export default function NewSubject() {
  const [tab, setTab] = useState<Tab>("upload");
  const [subjects, setSubjects] = useState<SubjectSummary[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    // An API that can't be reached shows through the health check instead.
    getJson<SubjectSummary[]>("/subjects", controller.signal)
      .then(setSubjects)
      .catch(() => {});
    return () => controller.abort();
  }, []);

  // Roving tabindex: Tab reaches the selected tab, the arrow keys move between them.
  function onTabKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next: Tab = tab === "upload" ? "topic" : "upload";
    setTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  }

  const tabButton = (value: Tab, label: string) => (
    <button
      type="button"
      role="tab"
      id={`tab-${value}`}
      aria-selected={tab === value}
      aria-controls={`panel-${value}`}
      tabIndex={tab === value ? 0 : -1}
      onClick={() => setTab(value)}
      className={styles.tab}
    >
      {label}
    </button>
  );

  return (
    <OnboardingShell
      subjects={subjects}
      eyebrow="Step 1 of 1 · New subject"
      title="What are you studying?"
      lede="Type a topic and Ebb will build questions from general knowledge, or upload your notes so every grade can cite them."
    >
      <div
        role="tablist"
        aria-label="How to add a subject"
        onKeyDown={onTabKey}
        className={styles.tabs}
      >
        {tabButton("upload", "Upload notes")}
        {tabButton("topic", "Type a topic")}
      </div>
      {/* Both stay mounted, so switching tabs keeps a draft's rows. */}
      <div
        role="tabpanel"
        id="panel-upload"
        aria-labelledby="tab-upload"
        hidden={tab !== "upload"}
        className={styles.panel}
      >
        <UploadPanel mode="draft" subjects={subjects} />
      </div>
      <div
        role="tabpanel"
        id="panel-topic"
        aria-labelledby="tab-topic"
        hidden={tab !== "topic"}
        className={styles.panel}
      >
        <TopicForm />
      </div>
    </OnboardingShell>
  );
}
