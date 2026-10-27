"use client";

import { useEffect, useRef, useState } from "react";
import ErrorCard from "@/components/ErrorCard";
import type { Card } from "@/lib/session";
import BottomSheet from "./BottomSheet";
import { shownCitations } from "./CitationChips";
import DisputeDialog from "./DisputeDialog";
import { checkingId, GRADE_NAME } from "./GradeCard";
import MemoryPanel, { MemoryPeek, MemorySheetBody, useConcepts } from "./MemoryPanel";
import SubjectColumn from "./SubjectColumn";
import Thread from "./Thread";
import useStudy from "./useStudy";
import styles from "./Workspace.module.css";

type Props = {
  subjectId: string;
  /** Whether the tutor has switched to its backup model, for the header's status. */
  onBackup: (backup: boolean) => void;
};

/** The dispute dialog's opening line: what was answered, and how it was graded. */
function disputeContext(cards: Card[], gradeId: string): string | null {
  const at = cards.findIndex((c) => c.id === gradeId);
  const grade = cards[at];
  if (grade?.kind !== "grade") return null;
  const question = cards.slice(0, at).findLast((c) => c.kind === "question");
  const fromNotes = shownCitations(grade.grade.citations).some((c) => c.kind !== "web");
  return (
    `Short answer on ${question?.concept ?? "this question"}, graded ${GRADE_NAME[grade.grade.value]}. ` +
    `A second grader will re-check it${fromNotes ? " against your notes" : ""}.`
  );
}

/**
 * The study workspace: subjects and sources, the thread, and the memory panel (a bottom
 * sheet at 375 px). A configuration error replaces them full width.
 */
export default function Workspace({ subjectId, onBackup }: Props) {
  const study = useStudy(subjectId);
  const memory = useConcepts(subjectId, study.updates);
  // The grade card the dispute dialog is open for.
  const [disputing, setDisputing] = useState<string | null>(null);
  const context = disputing ? disputeContext(study.cards, disputing) : null;

  // A dispute just sent: focus moves to its card's "re-checking" line, where the button was.
  const sent = useRef<string | null>(null);

  useEffect(() => onBackup(study.backup), [onBackup, study.backup]);
  useEffect(() => {
    if (!sent.current) return;
    document.getElementById(checkingId(sent.current))?.focus();
    sent.current = null;
  }, [study.cards]);

  if (study.fatal) {
    return (
      <div className={styles.full}>
        <ErrorCard title={study.fatal} />
      </div>
    );
  }
  return (
    <div className={styles.columns}>
      <SubjectColumn subjectId={subjectId} updates={study.updates} />
      <Thread study={study} onDispute={setDisputing} />
      <aside aria-label="Memory" className={styles.right}>
        <MemoryPanel concepts={memory.concepts} failed={memory.failed} onRetry={memory.retry} />
      </aside>
      <BottomSheet title="Memory" peek={<MemoryPeek concepts={memory.concepts} />}>
        <MemorySheetBody concepts={memory.concepts} subjectId={subjectId} failed={memory.failed} onRetry={memory.retry} />
      </BottomSheet>
      {disputing && context && (
        <DisputeDialog
          context={context}
          onCancel={() => setDisputing(null)}
          onSend={(reason, explanation) => {
            setDisputing(null);
            sent.current = disputing;
            study.dispute(disputing, reason, explanation);
          }}
        />
      )}
    </div>
  );
}
