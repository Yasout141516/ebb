"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getJson } from "@/lib/api";
import type { SubjectSummary } from "@/lib/types";
import Header from "./Header";

/** Sends the learner to /new with no subjects, else to the newest subject's study page. */
export default function HomeRedirect() {
  const router = useRouter();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getJson<SubjectSummary[]>("/subjects", controller.signal)
      .then((subjects) => router.replace(subjects.length === 0 ? "/new" : `/study/${subjects[0].id}`))
      .catch((error) => {
        if (error?.name !== "AbortError") setFailed(true);
      });
    return () => controller.abort();
  }, [router]);

  return (
    <>
      <Header backup={false} />
      {failed && <p style={{ padding: 24, color: "var(--ink-2)" }}>Can&apos;t reach the API.</p>}
    </>
  );
}
