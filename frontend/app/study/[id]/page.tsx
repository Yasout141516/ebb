"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import Header from "@/components/Header";
import HealthGate from "@/components/HealthGate";
import Workspace from "@/components/study/Workspace";
import styles from "./page.module.css";

/** A subject's study workspace. */
export default function StudySubject() {
  const { id } = useParams<{ id: string }>();
  const [backup, setBackup] = useState(false);
  return (
    <div className={styles.page}>
      <Header backup={backup} />
      <HealthGate>
        {/* A new subject is a new session: nothing carries over from the last one. */}
        <Workspace key={id} subjectId={id} onBackup={setBackup} />
      </HealthGate>
    </div>
  );
}
