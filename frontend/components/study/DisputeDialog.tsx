"use client";

import { useId, useRef, useState } from "react";
import { modalKeys, useFocusReturn } from "@/lib/modal";
import type { DisputeReason } from "@/lib/types";
import styles from "./DisputeDialog.module.css";

const REASONS: { value: DisputeReason; label: string }[] = [
  { value: "correct", label: "My answer is correct" },
  { value: "ambiguous", label: "The question is ambiguous" },
  { value: "notes_differ", label: "My notes say something different" },
  { value: "other", label: "Something else" },
];

type Props = {
  /** What is being disputed, e.g. "Short answer on DNS resolution, graded Partial. …" */
  context: string;
  onCancel: () => void;
  onSend: (reason: DisputeReason, explanation: string) => void;
};

/** tpl-Workspace's dispute dialog: why the grade is wrong, and an optional explanation. */
export default function DisputeDialog({ context, onCancel, onSend }: Props) {
  const [reason, setReason] = useState<DisputeReason>("correct");
  const [explanation, setExplanation] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const id = useId();
  useFocusReturn(first);

  return (
    <div className={styles.scrim}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onKeyDown={(e) => modalKeys(e, box.current, onCancel)}
        className={styles.dialog}
      >
        <div className={styles.head}>
          <h2 id={`${id}-title`} className={styles.title}>
            Dispute this grade
          </h2>
          <p className={styles.context}>{context}</p>
        </div>
        <fieldset className={styles.reasons}>
          <legend className={styles.legend}>What&apos;s wrong with it?</legend>
          {REASONS.map((r, i) => (
            <label key={r.value} className={`${styles.reason} ${reason === r.value ? styles.chosen : ""}`}>
              <input
                ref={i === 0 ? first : undefined}
                type="radio"
                name={`${id}-reason`}
                checked={reason === r.value}
                onChange={() => setReason(r.value)}
                className={styles.radio}
              />
              <span className={styles.reasonText}>{r.label}</span>
            </label>
          ))}
        </fieldset>
        <div className={styles.note}>
          <label htmlFor={`${id}-note`} className={styles.noteLabel}>
            Explain (optional)
          </label>
          <textarea
            id={`${id}-note`}
            rows={3}
            maxLength={2000}
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
            className={styles.textarea}
          />
        </div>
        <div className={styles.actions}>
          <button type="button" onClick={onCancel} className={`${styles.cancel} hov`}>
            Cancel
          </button>
          <button type="button" onClick={() => onSend(reason, explanation.trim())} className={`${styles.send} pbtn`}>
            Send for re-check
          </button>
        </div>
      </div>
    </div>
  );
}
