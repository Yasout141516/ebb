import styles from "./ErrorCard.module.css";

type Props = {
  title: string;
  text?: string;
  action?: { label: string; onClick: () => void };
};

/** A failure the learner can read, and usually act on. */
export default function ErrorCard({ title, text, action }: Props) {
  return (
    <div role="alert" className={styles.card}>
      <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" className={styles.icon}>
        <path d="M12 3 2 20h20L12 3z" />
        <path d="M12 10v4M12 17h.01" />
      </svg>
      <div className={styles.body}>
        <p className={styles.title}>{title}</p>
        {text && <p className={styles.text}>{text}</p>}
        {action && (
          <div className={styles.actions}>
            <button type="button" onClick={action.onClick} className={`${styles.action} pbtn`}>
              {action.label}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
