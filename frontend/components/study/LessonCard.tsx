import type { Lesson } from "@/lib/types";
import CitationChips from "./CitationChips";
import styles from "./LessonCard.module.css";

/** The short lesson after a miss, as a message from the tutor. */
export default function LessonCard({ lesson }: { lesson: Lesson }) {
  return (
    <section aria-label="Let's fix that" className={styles.lesson}>
      <span className={styles.name}>Ebb</span>
      <h3 className={styles.title}>Let&apos;s fix that</h3>
      <p className={styles.text}>{lesson.text}</p>
      {lesson.citations.length > 0 && (
        <div className={styles.sources}>
          <CitationChips citations={lesson.citations} />
        </div>
      )}
    </section>
  );
}
