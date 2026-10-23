import styles from "./NoticeDivider.module.css";

/** A notice as an inline divider in the thread (the prototype's <ModelNotice>). */
export default function NoticeDivider({ text }: { text: string }) {
  return (
    <div role="status" className={styles.divider}>
      <span className={styles.line} />
      <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" className={styles.icon}>
        <path d="M4 7h13l-3-3M20 17H7l3 3" />
      </svg>
      <span>{text}</span>
      <span className={styles.line} />
    </div>
  );
}
