"use client";

import { useRef, useState } from "react";
import type { Citation } from "@/lib/types";
import styles from "./CitationChips.module.css";

/** A citation the learner can open: one with a label and a quote. */
const usable = (c: Citation) => c.label.trim() !== "" && c.quote.trim() !== "";

/** The citations a card shows: the usable ones, notes first, then the web. */
export function shownCitations(citations: Citation[]): Citation[] {
  const valid = citations.filter(usable);
  return [...valid.filter((c) => c.kind !== "web"), ...valid.filter((c) => c.kind === "web")];
}

/** A chip's text, in the prototype's style: "Notes p.12", "Notes § TTL", "Notes", or the site. */
function chipLabel(c: Citation): string {
  if (c.kind === "web") return c.name || c.label;
  if (c.page !== null) return `Notes p.${c.page}`;
  return c.heading ? `Notes § ${c.heading}` : "Notes";
}

/** The "Sources" row of chips; a chip opens its label and quoted sentence. */
export default function CitationChips({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const chips = useRef<(HTMLButtonElement | null)[]>([]);
  const shown = shownCitations(citations);
  if (shown.length === 0) return null;

  function close() {
    const chip = chips.current[open ?? -1];
    setOpen(null);
    chip?.focus();
  }

  const current = open === null ? null : shown[open];
  return (
    <div className={styles.row}>
      <span className={styles.label}>Sources</span>
      {shown.map((c, i) => (
        <button
          key={i}
          ref={(el) => {
            chips.current[i] = el;
          }}
          type="button"
          onClick={() => setOpen(open === i ? null : i)}
          aria-expanded={open === i}
          aria-haspopup="dialog"
          title={`${c.kind === "web" ? "From the web" : "From your notes"} · ${c.label}`}
          className={`${styles.chip} hov`}
        >
          [{chipLabel(c)}]
        </button>
      ))}
      {current && (
        <div
          role="dialog"
          aria-label="Source snippet"
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
          }}
          className={styles.popover}
        >
          <div className={styles.popHead}>
            <span className={styles.popLabel}>{current.label}</span>
            <button
              type="button"
              onClick={close}
              aria-label="Close source snippet"
              autoFocus
              className={`${styles.close} hov`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
          <blockquote className={styles.quote}>
            “<mark className={styles.mark}>{current.quote}</mark>”
          </blockquote>
        </div>
      )}
    </div>
  );
}
