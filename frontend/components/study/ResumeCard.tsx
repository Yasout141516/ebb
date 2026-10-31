import { formatAgo } from "@/lib/format";
import type { Offer } from "@/lib/types";
import styles from "./ResumeCard.module.css";

export const BUSY = "That session is open in another tab or terminal.";

type Props = {
  offer: Offer;
  now?: Date;
  onResume: () => void;
  onStartFresh: () => void;
};

/** An open session to carry on with, or (driven elsewhere) only to replace. */
export default function ResumeCard({ offer, now = new Date(), onResume, onStartFresh }: Props) {
  // Number 0: paused on the concept's lesson, before its questions.
  const where = offer.number === 0 ? `the lesson for ${offer.concept}` : `${offer.concept}, question ${offer.number}`;
  const text = offer.busy ? BUSY : `You stopped on ${where}, ${formatAgo(offer.last_active_at, now)}.`;
  return (
    <div className={styles.card}>
      <span className={styles.name}>Ebb</span>
      <p className={styles.text}>{text}</p>
      <div className={styles.actions}>
        {!offer.busy && (
          <button type="button" onClick={onResume} className={`${styles.primary} pbtn`}>
            Resume
          </button>
        )}
        <button type="button" onClick={onStartFresh} className={`${styles.secondary} hov`}>
          Start fresh
        </button>
      </div>
    </div>
  );
}
