"use client";

import { useEffect, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import Thread from "./Thread";
import useStudy from "./useStudy";
import styles from "./Workspace.module.css";

type Props = {
  subjectId: string;
  /** Whether the tutor has switched to its backup model, for the header's status. */
  onBackup: (backup: boolean) => void;
};

/** The study workspace's three columns; a configuration error replaces them full width. */
export default function Workspace({ subjectId, onBackup }: Props) {
  const study = useStudy(subjectId);
  // The grade card the dispute dialog is open for.
  const [, setDisputing] = useState<string | null>(null);

  useEffect(() => onBackup(study.backup), [onBackup, study.backup]);

  if (study.fatal) {
    return (
      <div className={styles.full}>
        <ErrorCard title={study.fatal} />
      </div>
    );
  }
  return (
    <div className={styles.columns}>
      <aside aria-label="Subjects and sources" className={styles.left} />
      <Thread study={study} onDispute={setDisputing} />
      <aside aria-label="Memory" className={styles.right} />
    </div>
  );
}
