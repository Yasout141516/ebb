"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, postJson } from "@/lib/api";
import type { CreatedSubject } from "@/lib/types";
import shared from "./shared.module.css";
import StartButton from "./StartButton";
import styles from "./TopicForm.module.css";

const SUGGESTIONS = ["Computer Networks", "Operating Systems", "Linear Algebra"];

/** "Type a topic": the topic is the subject's name, as in `ebb new "<topic>"`. */
export default function TopicForm() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [mapping, setMapping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setMapping(true);
    setError(null);
    try {
      const subject = await postJson<CreatedSubject>("/subjects", { topic: topic.trim() });
      router.push(`/study/${subject.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Can't reach the API.");
      setMapping(false);
    }
  }

  function pick(value: string) {
    setTopic(value);
    setError(null);
  }

  return (
    <>
      <section className={styles.card}>
        <label htmlFor="topic" className={styles.label}>
          Topic
        </label>
        <input
          id="topic"
          type="text"
          value={topic}
          onChange={(e) => pick(e.target.value)}
          readOnly={mapping}
          placeholder="e.g. Computer Networks"
          className={styles.input}
        />
        {error && (
          <p role="alert" className={shared.fieldError}>
            {error}
          </p>
        )}
        <div className={styles.suggestions}>
          <span className={styles.try}>Try:</span>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => pick(s)}
              disabled={mapping}
              className={`${styles.chip} hov`}
            >
              {s}
            </button>
          ))}
        </div>
      </section>
      <div className={shared.footer}>
        <StartButton
          disabled={topic.trim() === ""}
          onClick={start}
          busyLabel={mapping ? "Mapping the topic…" : undefined}
        />
      </div>
    </>
  );
}
