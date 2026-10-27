import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DueToday from "./DueToday";

const due = [
  { name: "Routing", tide: 0.44 },
  { name: "Subnetting", tide: 0.29 },
  { name: "DNS resolution", tide: 0.52 },
];

describe("DueToday", () => {
  it("lists what's due, lowest tide first, with Review these now", () => {
    render(<DueToday due={due} nextReview={null} subjectId="s1" />);
    expect(screen.getByRole("heading", { name: "Due today" })).toBeInTheDocument();
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(["Subnetting29%", "Routing44%", "DNS resolution52%"]);
    expect(screen.getByRole("link", { name: "Review these now" })).toHaveAttribute("href", "/study/s1");
    expect(screen.queryByText(/minutes/)).not.toBeInTheDocument();
  });

  it("with nothing due says when the next review is", () => {
    render(<DueToday due={[]} nextReview="2026-10-29T09:00:00Z" subjectId="s1" />);
    expect(screen.getByText("Tide's high. Next review Thursday.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("with nothing studied yet says so", () => {
    render(<DueToday due={[]} nextReview={null} subjectId="s1" />);
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });

  it("at 375 px counts them in the heading and offers the review as a button", () => {
    render(<DueToday due={due} nextReview={null} subjectId="s1" compact />);
    expect(screen.getByRole("heading", { name: "Due today · 3" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review these now" })).toHaveAttribute("href", "/study/s1");
  });
});
