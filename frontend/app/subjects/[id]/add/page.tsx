"use client";

import { useParams } from "next/navigation";
import ErrorCard from "@/components/ErrorCard";
import OnboardingShell from "@/components/onboarding/OnboardingShell";
import UploadPanel from "@/components/onboarding/UploadPanel";
import { getJson } from "@/lib/api";
import type { Source, SubjectSummary } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Loaded = { subjects: SubjectSummary[]; subject: SubjectSummary; sources: Source[] };

/** Add source: the onboarding upload screen in "add to {subject}" mode, as `ebb add`. */
export default function AddSource() {
  const { id } = useParams<{ id: string }>();
  const { loaded, retry } = useLoad<Loaded>(async (signal) => {
    const [subjects, sources] = await Promise.all([
      getJson<SubjectSummary[]>("/subjects", signal),
      getJson<Source[]>(`/subjects/${id}/sources`, signal),
    ]);
    const subject = subjects.find((s) => s.id === id);
    return subject ? { subjects, subject, sources } : "missing";
  }, id);

  if (loaded === null || loaded === "missing" || loaded === "failed") {
    return (
      <OnboardingShell subjects={[]} eyebrow="Sources" title="Add notes" lede="">
        {loaded === "missing" && <ErrorCard title="Not found." />}
        {loaded === "failed" && (
          <ErrorCard
            title="Couldn't load this subject's sources."
            action={{ label: "Retry", onClick: retry }}
          />
        )}
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
