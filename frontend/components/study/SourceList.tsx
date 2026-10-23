import Link from "next/link";
import ErrorCard from "@/components/ErrorCard";
import { kindLabel, MAX_FILES } from "@/lib/notes";
import type { Source } from "@/lib/types";
import styles from "./SourceList.module.css";

function Plus() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className={styles.plus}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

type Props = {
  subjectId: string;
  /** Null until they first arrive. */
  sources: Source[] | null;
  /** Loading them failed: with no sources yet, an error card with Retry shows. */
  failed?: boolean;
  onRetry?: () => void;
};

/** The left column's Sources: each file with its detail line, and Add source (D5's 5 of 5). */
export default function SourceList({ subjectId, sources, failed = false, onRetry }: Props) {
  const add = `/subjects/${subjectId}/add`;
  if (sources === null) {
    // How many files there are is unknown: no Add source that might be over the limit.
    return (
      <section className={styles.section}>
        <h2 className={styles.heading}>Sources</h2>
        {failed && (
          <ErrorCard
            title="Couldn't load your sources."
            action={onRetry && { label: "Retry", onClick: onRetry }}
          />
        )}
      </section>
    );
  }
  if (sources.length === 0) {
    return (
      <section className={styles.section}>
        <h2 className={styles.heading}>Sources</h2>
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>No sources yet</p>
          <p className={styles.emptyText}>
            Grades will be marked “General knowledge” until you add notes. PDF, TXT or MD, up to 20 MB.
          </p>
          <Link href={add} className={styles.emptyLink}>
            Upload notes
          </Link>
        </div>
      </section>
    );
  }
  const full = sources.length >= MAX_FILES;
  return (
    <section className={styles.section}>
      <h2 className={styles.heading}>Sources</h2>
      {sources.map((s) => (
        <div key={s.id} className={styles.source}>
          <span aria-hidden="true" className={styles.badge}>
            {kindLabel(s.filename)}
          </span>
          <span className={styles.text}>
            <span className={styles.filename}>{s.filename}</span>
            <span className={styles.detail}>{s.detail}</span>
          </span>
        </div>
      ))}
      {full ? (
        <span role="link" aria-disabled="true" className={`${styles.add} ${styles.disabled}`}>
          <Plus />
          {MAX_FILES} of {MAX_FILES} files
        </span>
      ) : (
        <Link href={add} className={`${styles.add} hov`}>
          <Plus />
          Add source
        </Link>
      )}
      <p className={styles.hint}>PDF, TXT, MD · max 20 MB. Don&apos;t upload sensitive documents.</p>
    </section>
  );
}
