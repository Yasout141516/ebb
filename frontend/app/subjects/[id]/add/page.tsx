"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import OnboardingShell from "@/components/onboarding/OnboardingShell";
import UploadPanel from "@/components/onboarding/UploadPanel";
import { getJson } from "@/lib/api";
import type { Source, SubjectSummary } from "@/lib/types";

type Loaded = { subjects: SubjectSummary[]; subject: SubjectSummary; sources: Source[] };

/** Add source: the onboarding upload screen in "add to {subject}" mode, as `ebb add`. */
export default function AddSource() {
  const { id } = useParams<{ id: string }>();
  const [loaded, setLoaded] = useState<Loaded | "missing" | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getJson<SubjectSummary[]>("/subjects", controller.signal),
      getJson<Source[]>(`/subjects/${id}/sources`, controller.signal),
    ])
      .then(([subjects, sources]) => {
        const subject = subjects.find((s) => s.id === id);
        setLoaded(subject ? { subjects, subject, sources } : "missing");
      })
      .catch((error) => {
        // An API that can't be reached shows through the health check instead.
        if (error?.status === 404 || error?.status === 422) setLoaded("missing");
      });
    return () => controller.abort();
  }, [id]);

  if (loaded === null || loaded === "missing") {
    return (
      <OnboardingShell subjects={[]} eyebrow="Sources" title="Add notes" lede="">
        {loaded === "missing" && <ErrorCard title="Not found." />}
      </OnboardingShell>
    );
  }
  return (
    <OnboardingShell
      subjects={loaded.subjects}
      eyebrow={`Sources · ${loaded.subject.name}`}
      title={`Add notes to ${loaded.subject.name}`}
      lede="One file at a time. Each is saved as soon as its concepts are found, so every grade can cite it."
    >
      <UploadPanel mode="add" subject={loaded.subject} sources={loaded.sources} />
    </OnboardingShell>
  );
}
