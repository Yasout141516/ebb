"use client";

import { type ReactNode, useId, useRef, useState } from "react";
import { modalKeys, useFocusReturn } from "./DisputeDialog";
import styles from "./BottomSheet.module.css";

type Props = {
  title: string;
  /** What the closed sheet shows beside its title. */
  peek: ReactNode;
  children: ReactNode;
};

/** The open sheet: a modal over the page, focused on its close button. */
function Sheet({ id, title, onClose, children }: { id: string; title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useFocusReturn(close);
  return (
    <>
      <div aria-hidden="true" className={styles.scrim} onClick={onClose} />
      <section
        ref={box}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onKeyDown={(e) => modalKeys(e, box.current, onClose)}
        className={styles.sheet}
      >
        <div className={styles.top}>
          <span aria-hidden="true" className={styles.grabber} />
          <div className={styles.titleRow}>
            <h2 id={`${id}-title`} className={styles.title}>
              {title}
            </h2>
            <button
              ref={close}
              type="button"
              onClick={onClose}
              aria-label={`Close ${title.toLowerCase()} panel`}
              className={`${styles.close} hov`}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className={styles.cross}>
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
        </div>
        <div className={styles.body}>{children}</div>
      </section>
    </>
  );
}

/** tpl-WorkspaceMobile's bottom sheet: a peek bar at the foot of the screen that opens it (D6). */
export default function BottomSheet({ title, peek, children }: Props) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={styles.root}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        aria-controls={id}
        className={styles.peek}
      >
        <span aria-hidden="true" className={styles.grabber} />
        <span className={styles.peekRow}>
          <span className={styles.peekTitle}>{title}</span>
          {peek}
        </span>
      </button>
      {open && (
        <Sheet id={id} title={title} onClose={() => setOpen(false)}>
          {children}
        </Sheet>
      )}
    </div>
  );
}
