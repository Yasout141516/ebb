"use client";

import { useRouter } from "next/navigation";
import type { SubjectSummary } from "@/lib/types";
import styles from "./SubjectPicker.module.css";

type Props = { subjects: SubjectSummary[]; current: string };

/**
 * The subject the dashboard shows, in the eyebrow's place ("Computer Networks · last 30 days"):
 * a native select dressed as that text. Picking another opens its progress page.
 */
export default function SubjectPicker({ subjects, current }: Props) {
  const router = useRouter();
  return (
    <span className={styles.wrap}>
      <select
        aria-label="Subject"
        value={current}
        onChange={(e) => router.push(`/progress/${e.target.value}`)}
        className={styles.select}
      >
        {subjects.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true" className={styles.chevron}>
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}
