"use client";

import { useRef, useState } from "react";
import styles from "./DropZone.module.css";

type Props = {
  /** "Drop lecture notes here", or "5 of 5 files" when the subject is full. */
  title: string;
  disabled: boolean;
  multiple: boolean;
  onFiles: (files: File[]) => void;
};

/** Where notes are dropped or chosen. */
export default function DropZone({ title, disabled, multiple, onFiles }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function take(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (!disabled && files.length > 0) onFiles(multiple ? files : files.slice(0, 1));
  }

  return (
    <div
      aria-disabled={disabled || undefined}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
      className={`${styles.zone} ${over ? styles.over : ""} ${disabled ? styles.disabled : ""}`}
    >
      <svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true" className={styles.icon}>
        <path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" />
      </svg>
      <p className={styles.title}>{title}</p>
      <p className={styles.limits}>PDF, TXT or MD · up to 20 MB per file · 5 files per subject</p>
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className={`${styles.choose} hov`}
      >
        Choose file
      </button>
      <input
        ref={input}
        type="file"
        accept=".pdf,.txt,.md"
        multiple={multiple}
        disabled={disabled}
        tabIndex={-1}
        aria-hidden="true"
        className={styles.input}
        onChange={(e) => {
          take(e.target.files);
          e.target.value = ""; // the same file can be chosen again
        }}
      />
    </div>
  );
}
