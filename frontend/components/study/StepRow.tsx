import styles from "./StepRow.module.css";

/** The step the tutor is on, until its cards arrive ("Ebb · Reading your notes…"). */
export default function StepRow({ label }: { label: string }) {
  return (
    <div aria-live="polite" className={styles.row}>
      <span className={styles.name}>
        Ebb <span className={styles.label}>· {label}</span>
      </span>
      <p className={styles.text}>
        <span className="caret" aria-hidden="true" />
      </p>
    </div>
  );
}
