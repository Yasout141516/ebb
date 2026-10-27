import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Finished } from "@/lib/types";
import SummaryCard from "./SummaryCard";

const finished = (over: Partial<Finished> = {}): Finished => ({
  results: [
    {
      concept: "Subnetting",
      concept_id: "c1",
      tally: { correct: 2, partial: 1, incorrect: 0 },
      stage_before: 1,
      stage_after: 2,
      tide_before: 0.29,
      tide_after: 0.61,
      next_review_at: "2026-10-29T09:00:00Z",
    },
  ],
  final: { grade: null, lesson: null, notices: [] },
  caught_up: false,
  next_review_at: "2026-10-29T09:00:00Z",
  next_review_line: "Next review: Thursday · 3 concepts",
  ...over,
});

describe("SummaryCard", () => {
  it("lists each concept's answers, tide, stage and next review, and D4's line", () => {
    render(<SummaryCard finished={finished()} timeZone="UTC" />);
    const row = screen.getByRole("row", { name: /Subnetting/ });
    expect(within(row).getByText("2✓ 1◐ 0✗")).toBeInTheDocument();
    expect(within(row).getByText("29% → 61%")).toBeInTheDocument();
    expect(within(row).getByText("●○○○○ → ●●○○○")).toBeInTheDocument();
    expect(within(row).getByText("Thu 29 Oct")).toBeInTheDocument();
    expect(screen.getByText("Next review: Thursday · 3 concepts")).toBeInTheDocument();
  });

  it("says so when there were no answers", () => {
    render(<SummaryCard finished={finished({ results: [], next_review_line: null })} timeZone="UTC" />);
    expect(screen.getByText("No answers this session. Your memory levels are unchanged.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says when everything is caught up", () => {
    render(<SummaryCard finished={finished({ caught_up: true })} timeZone="UTC" />);
    expect(screen.getByText("You're all caught up — next review Thu 29 Oct.")).toBeInTheDocument();
    render(<SummaryCard finished={finished({ caught_up: true, next_review_at: null })} timeZone="UTC" />);
    expect(screen.getByText("You're all caught up.")).toBeInTheDocument();
  });
});
