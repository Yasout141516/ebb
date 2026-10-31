import styles from "./ReadyCard.module.css";

type Props = {
  onReady: () => void;
  disabled?: boolean;
};

/** Under the learn phase: on to the quiz. */
export default function ReadyCard({ onReady, disabled = false }: Props) {
  return (
    <section aria-label="Ready for the quiz" className={styles.card}>
      <div className={styles.text}>
        <h2 className={styles.title}>Ready to be quizzed?</h2>
        <p className={styles.note}>
          Ebb starts with multiple choice. Questions you asked here do not change your tide or your review schedule.
        </p>
      </div>
      <button type="button" onClick={onReady} disabled={disabled} className={`${styles.ready} pbtn`}>
        I&apos;m ready, quiz me
      </button>
    </section>
  );
}
