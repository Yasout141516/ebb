import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Stage } from "@/lib/types";
import StageColumns, { nextText } from "./StageColumns";

const NOW = new Date(2026, 9, 27, 12, 0);
const at = (days: number, hour = 9) => new Date(2026, 9, 27 + days, hour, 0).toISOString();

const stages: Stage[] = [
  { stage: 1, concepts: [{ name: "Subnetting", next_review_at: at(-1) }] },
  { stage: 2, concepts: [{ name: "Routing", next_review_at: at(0) }] },
  { stage: 3, concepts: [{ name: "UDP", next_review_at: at(1) }] },
  { stage: 4, concepts: [] },
  { stage: 5, concepts: [{ name: "OSI layers", next_review_at: at(26) }] },
];
const tides = new Map<string, number | null>([
  ["Subnetting", 0.29],
  ["Routing", 0.44],
  ["UDP", 0.63],
  ["OSI layers", 0.92],
]);

describe("StageColumns", () => {
  it("shows a column per stage with its gap, and says when one is empty", () => {
    render(<StageColumns stages={stages} tides={tides} now={NOW} />);
    expect(screen.getByRole("heading", { name: "Review stages" })).toBeInTheDocument();
    const four = screen.getByRole("heading", { name: "Stage 4" }).closest("div")!.parentElement!;
    expect(within(four).getByText("back after 16 days")).toBeInTheDocument();
    expect(within(four).getByText("Nothing here yet.")).toBeInTheDocument();
    expect(screen.getAllByText("Nothing here yet.")).toHaveLength(1);
    expect(screen.getByText("back after 1 day")).toBeInTheDocument();
  });

  it("says when each concept comes back", () => {
    render(<StageColumns stages={stages} tides={tides} now={NOW} />);
    expect(screen.getByText("Overdue 1 day")).toBeInTheDocument();
    expect(screen.getByText("Due today")).toBeInTheDocument();
    expect(screen.getByText("Tomorrow")).toBeInTheDocument();
    expect(screen.getByText("Next in 26 days")).toBeInTheDocument();
  });

  it("at 375 px lists the names as chips, empty stages still saying so", () => {
    render(<StageColumns stages={stages} tides={tides} now={NOW} compact />);
    expect(screen.getByText("Subnetting")).toBeInTheDocument();
    expect(screen.queryByText("Tomorrow")).not.toBeInTheDocument();
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });

  it("nextText counts calendar days in the browser's zone", () => {
    expect(nextText(at(-3), NOW)).toEqual({ text: "Overdue 3 days", due: true });
    expect(nextText(at(0, 23), NOW)).toEqual({ text: "Due today", due: true });
    expect(nextText(null, NOW)).toEqual({ text: "Due today", due: true });
    expect(nextText(at(2), NOW)).toEqual({ text: "Next in 2 days", due: false });
  });
});
