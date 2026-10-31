"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./Composer.module.css";

type Props = {
  /** Only while a short-answer question waits, or a lesson takes questions. */
  enabled: boolean;
  onSend: (text: string) => void;
  label?: string;
  placeholder?: string;
  /** Take focus when enabled (not while an error card's Retry has it). */
  autoFocus?: boolean;
};

/** The answer box at the bottom of the thread: Enter sends, Shift+Enter starts a new line. */
export default function Composer({
  enabled,
  onSend,
  label = "Your answer",
  placeholder = "Type your answer…",
  autoFocus = true,
}: Props) {
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const canSend = enabled && draft.trim() !== "";

  useEffect(() => {
    if (enabled && autoFocus) box.current?.focus();
  }, [enabled, autoFocus]);

  function send() {
    if (!canSend) return;
    onSend(draft.trim());
    setDraft("");
  }

  return (
    <div className={styles.bar}>
      <div className={styles.inner}>
        <div className={styles.box}>
          <label htmlFor="composer" className={styles.hidden}>
            {label}
          </label>
          <textarea
            id="composer"
            ref={box}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={placeholder}
            disabled={!enabled}
            className={styles.input}
          />
          <button type="button" onClick={send} aria-label="Send" disabled={!canSend} className={`${styles.send} pbtn`}>
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 19V5M6 11l6-6 6 6" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
