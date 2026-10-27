import styles from "./shared.module.css";

/** The bottom bar's Start studying button, with the prototype's arrow. */
export default function StartButton({
  disabled,
  onClick,
  busyLabel,
}: {
  disabled: boolean;
  onClick: () => void;
  busyLabel?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busyLabel !== undefined}
      className={`${styles.primary} pbtn`}
    >
      {busyLabel !== undefined ? (
        <span role="status" className={styles.busy}>
          {busyLabel}
        </span>
      ) : (
        <>
          Start studying
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className={styles.arrow}>
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </>
      )}
    </button>
  );
}
