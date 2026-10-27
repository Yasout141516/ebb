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
  ["NAT", null],
]);

describe("StageColumns", () => {
  it("shows a column per stage with its gap, and says when one is empty", () => {
    render(<StageColumns stages={stages} notStarted={["NAT"]} tides={tides} now={NOW} />);
    expect(screen.getByRole("heading", { name: "Review stages" })).toBeInTheDocument();
    const four = screen.getByRole("group", { name: "Stage 4" });
    expect(within(four).getByText("back after 16 days")).toBeInTheDocument();
    expect(within(four).getByText("Nothing here yet.")).toBeInTheDocument();
    expect(screen.getAllByText("Nothing here yet.")).toHaveLength(1);
    expect(within(screen.getByRole("group", { name: "Stage 1" })).getByText("back after 1 day")).toBeInTheDocument();
  });

  it("ends with the concepts not started yet, as the prototype's sixth column", () => {
    render(<StageColumns stages={stages} notStarted={["NAT", "ARP"]} tides={tides} now={NOW} />);
    const groups = screen.getAllByRole("group");
    expect(groups.map((g) => g.querySelector("h3")!.textContent)).toEqual([
      "Stage 1",
      "Stage 2",
      "Stage 3",
      "Stage 4",
      "Stage 5",
      "Not started",
    ]);
    const waiting = screen.getByRole("group", { name: "Not started" });
    expect(within(waiting).getByText("no reviews yet")).toBeInTheDocument();
    expect(within(waiting).getByText("NAT")).toBeInTheDocument();
    expect(within(waiting).getByText("ARP")).toBeInTheDocument();
    // Each item's own line says it hasn't started, beside the column's heading.
    expect(within(waiting).getAllByText("Not started")).toHaveLength(3);
  });

  it("an empty Not started column says so too", () => {
    render(<StageColumns stages={stages} notStarted={[]} tides={tides} now={NOW} />);
    const waiting = screen.getByRole("group", { name: "Not started" });
    expect(within(waiting).getByText("Nothing here yet.")).toBeInTheDocument();
  });

  it("says when each concept comes back", () => {
    render(<StageColumns stages={stages} notStarted={[]} tides={tides} now={NOW} />);
    expect(screen.getByText("Overdue 1 day")).toBeInTheDocument();
    expect(screen.getByText("Due today")).toBeInTheDocument();
    expect(screen.getByText("Tomorrow")).toBeInTheDocument();
    expect(screen.getByText("Next in 26 days")).toBeInTheDocument();
  });

  it("at 375 px lists the names as chips, with a Not started row", () => {
    render(<StageColumns stages={stages} notStarted={["NAT"]} tides={tides} now={NOW} compact />);
    expect(within(screen.getByRole("group", { name: "Stage 1" })).getByText("Subnetting")).toBeInTheDocument();
    expect(screen.queryByText("Tomorrow")).not.toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Stage 4" })).getByText("Nothing here yet.")).toBeInTheDocument();
    const waiting = screen.getByRole("group", { name: "Not started" });
    expect(within(waiting).getByText("no reviews yet")).toBeInTheDocument();
    expect(within(waiting).getByText("NAT")).toBeInTheDocument();
  });

  it("nextText counts calendar days in the browser's zone", () => {
    expect(nextText(at(-3), NOW)).toEqual({ text: "Overdue 3 days", due: true });
    expect(nextText(at(0, 23), NOW)).toEqual({ text: "Due today", due: true });
    expect(nextText(null, NOW)).toEqual({ text: "Due today", due: true });
    expect(nextText(at(2), NOW)).toEqual({ text: "Next in 2 days", due: false });
  });
});
