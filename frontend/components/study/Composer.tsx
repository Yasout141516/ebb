"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./Composer.module.css";

type Props = {
  /** Only while a short-answer question waits. */
  enabled: boolean;
  onSend: (text: string) => void;
};

/** The answer box at the bottom of the thread: Enter sends, Shift+Enter starts a new line. */
export default function Composer({ enabled, onSend }: Props) {
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const canSend = enabled && draft.trim() !== "";

  useEffect(() => {
    if (enabled) box.current?.focus();
  }, [enabled]);

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
            Your answer
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
            placeholder="Type your answer…"
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
