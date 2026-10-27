"use client";

import { useEffect, useState } from "react";
import { getJson } from "@/lib/api";
import type { Health } from "@/lib/types";
import ErrorCard from "./ErrorCard";
import styles from "./HealthGate.module.css";

type State = { kind: "checking" | "ok" | "unreachable" } | { kind: "down"; message: string };

/**
 * Checks GET /health on load (ruling R6). While the database, the tutor's setup or the
 * time zone setting is broken, its message replaces the page full width; an API that can't be reached gets
 * an error card with Retry.
 */
export default function HealthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>({ kind: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getJson<Health>("/health", controller.signal)
      .then((health) => {
        if (health.tutor !== "ok") setState({ kind: "down", message: health.tutor });
        else if (health.database !== "ok") setState({ kind: "down", message: health.database });
        else if (health.timezone !== "ok") setState({ kind: "down", message: health.timezone });
        else setState({ kind: "ok" });
      })
      .catch((error) => {
        if (error?.name !== "AbortError") setState({ kind: "unreachable" });
      });
    return () => controller.abort();
  }, [attempt]);

  if (state.kind === "down") {
    return (
      <div className={styles.full}>
        <ErrorCard title={state.message} />
      </div>
    );
  }
  if (state.kind === "unreachable") {
    return (
      <div className={styles.full}>
        <ErrorCard
          title="Can't reach the API."
          action={{ label: "Retry", onClick: () => setAttempt((n) => n + 1) }}
        />
      </div>
    );
  }
  return children;
}
