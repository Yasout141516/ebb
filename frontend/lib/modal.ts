import { type KeyboardEvent, type RefObject, useEffect } from "react";

const FOCUSABLE = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]';

/**
 * A modal's keyboard rules: Tab and Shift+Tab stay inside `box`, Escape calls `close`.
 * A radio group counts once, as the browser tabs through it.
 */
export function modalKeys(event: KeyboardEvent, box: HTMLElement | null, close: () => void) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab" || !box) return;

  // Every element, filtered, keeps document order (a selector list needn't, in jsdom).
  const stops = [...box.querySelectorAll<HTMLElement>("*")].filter(
    (el) => el.matches(FOCUSABLE) && !(el instanceof HTMLInputElement && el.type === "radio" && !el.checked),
  );
  if (stops.length === 0) return;
  const first = stops[0];
  const last = stops[stops.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** Focus `target` once mounted, and give focus back to whatever had it when this closes. */
export function useFocusReturn(target: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    target.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, [target]);
}
