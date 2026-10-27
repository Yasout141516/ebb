import { useEffect, useEffectEvent, useState } from "react";

/** What a page has so far: nothing yet, what it loaded, "missing" (404/422) or "failed". */
export type Load<T> = T | "missing" | "failed" | null;

/**
 * Load a page's data in the browser: `load` runs again when `key` changes (and on `retry`),
 * aborts with the page, and may answer "missing" itself. A 404 or 422 is "missing"; any other
 * failure is "failed".
 */
export function useLoad<T extends object>(load: (signal: AbortSignal) => Promise<T | "missing">, key: string) {
  const [loaded, setLoaded] = useState<Load<T>>(null);
  const [attempt, setAttempt] = useState(0);
  const run = useEffectEvent(load);

  useEffect(() => {
    const controller = new AbortController();
    run(controller.signal)
      .then(setLoaded)
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setLoaded(error?.status === 404 || error?.status === 422 ? "missing" : "failed");
      });
    return () => controller.abort();
  }, [key, attempt]);

  return {
    loaded,
    retry: () => {
      setLoaded(null);
      setAttempt((n) => n + 1);
    },
  };
}
