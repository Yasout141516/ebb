import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation, StoredLesson } from "@/lib/types";
import LearnLesson, { LESSON_LABEL } from "./LearnLesson";

const notes: Citation = {
  kind: "chunk",
  ref: "k1",
  label: "Week_1.pdf, p. 14",
  page: 14,
  quote: "The SYN carries the starting number.",
  name: "Week_1.pdf",
  heading: null,
  title: null,
};

const web: Citation = {
  ...notes,
  kind: "web",
  ref: "https://example.com/tcp",
  label: "TCP explained",
  page: null,
  quote: "Three segments open a connection.",
  name: "example.com",
  title: "TCP explained",
};

const lesson = (over: Partial<StoredLesson> = {}): StoredLesson => ({
  concept_id: "c1",
  markdown: "# TCP handshake\n\n**In one sentence:** three messages open a connection.",
  citations: [notes, web],
  web_pages: [],
  suggestions: [],
  checks: ["Why can't the server skip the SYN-ACK?", "What ack follows a SYN numbered 1000?"],
  source_summary: { notes: 2, notes_pages: [12, 13, 14, 15], web_pages: 2, label: null },
  model: "m",
  written_at: "2026-10-31T09:00:00Z",
  learned_at: null,
  ...over,
});

describe("LearnLesson", () => {
  it("shows its label, sources, the lesson and the check-yourself questions, unanswered", async () => {
    const onAsk = vi.fn();
    render(<LearnLesson label={LESSON_LABEL} lesson={lesson()} onAsk={onAsk} />);
    const card = screen.getByRole("article", { name: "Lesson" });
    expect(within(card).getByText("Lesson · quiz opens when you press Ready")).toBeInTheDocument();
    expect(within(card).getByText("Your notes · pp. 12 to 15")).toBeInTheDocument();
    expect(within(card).getByText("2 web sources")).toBeInTheDocument();
    expect(within(card).getByRole("heading", { name: "TCP handshake", level: 1 })).toBeInTheDocument();
    expect(within(card).getByRole("heading", { name: "Check yourself" })).toBeInTheDocument();
    expect(within(card).getByText("Why can't the server skip the SYN-ACK?")).toBeInTheDocument();
    expect(within(card).getAllByRole("button", { name: "Answer this for me" })).toHaveLength(2);
    expect(within(card).getByRole("button", { name: "[Notes p.14]" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "[example.com]" })).toBeInTheDocument();
    expect(within(card).queryByText("Not in your notes")).not.toBeInTheDocument();
    await userEvent.click(within(card).getAllByRole("button", { name: "Answer this for me" })[1]);
    expect(onAsk).toHaveBeenCalledWith("What ack follows a SYN numbered 1000?");
  });

  it("drops the button of a question already asked, and every button when read-only", () => {
    const { rerender } = render(
      <LearnLesson label={LESSON_LABEL} lesson={lesson()} onAsk={() => {}} asked={["Why can't the server skip the SYN-ACK?"]} />,
    );
    expect(screen.getAllByRole("button", { name: "Answer this for me" })).toHaveLength(1);
    rerender(<LearnLesson label="Lesson" lesson={lesson()} />);
    expect(screen.queryByRole("button", { name: "Answer this for me" })).not.toBeInTheDocument();
    expect(screen.getByText("What ack follows a SYN numbered 1000?")).toBeInTheDocument();
  });

  it("marks a lesson with no notes citation, when the subject has notes", () => {
    const summary = { notes: 1, notes_pages: [14], web_pages: 0, label: "Your notes only" };
    const { rerender } = render(
      <LearnLesson label={LESSON_LABEL} lesson={lesson({ citations: [web], source_summary: summary })} />,
    );
    expect(screen.getByText("Your notes only")).toBeInTheDocument();
    expect(screen.getByText("Not in your notes")).toBeInTheDocument();
    const general = { notes: 0, notes_pages: [], web_pages: 0, label: "General knowledge · not from your notes" };
    rerender(<LearnLesson label={LESSON_LABEL} lesson={lesson({ citations: [], source_summary: general })} />);
    expect(screen.getByText("General knowledge · not from your notes")).toBeInTheDocument();
    expect(screen.queryByText("Not in your notes")).not.toBeInTheDocument();
  });

  it("names one page, or none, and one web source", () => {
    const summary = { notes: 1, notes_pages: [14], web_pages: 1, label: null };
    const { rerender } = render(<LearnLesson label={LESSON_LABEL} lesson={lesson({ source_summary: summary })} />);
    expect(screen.getByText("Your notes · p. 14")).toBeInTheDocument();
    expect(screen.getByText("1 web source")).toBeInTheDocument();
    rerender(
      <LearnLesson label={LESSON_LABEL} lesson={lesson({ source_summary: { ...summary, notes_pages: [] } })} />,
    );
    expect(screen.getByText("Your notes")).toBeInTheDocument();
  });

  it("shows the text streaming in, in a live region, before the lesson is kept", () => {
    render(<LearnLesson label={LESSON_LABEL} lesson={null} live={"# TCP handshake\n\nThree mess"} />);
    const live = screen.getByText("Three mess").closest("[aria-live]");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("heading", { name: "Check yourself" })).not.toBeInTheDocument();
  });
});
