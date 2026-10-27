import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StreakCard from "./StreakCard";

// Five weeks, oldest first: 21 quiet days, then 9 of the last 14 studied, the last 5 in a row up to today.
const recent = [true, false, true, true, false, true, false, false, false, true, true, true, true, true];
const days = [...Array(21).fill(false), ...recent];

describe("StreakCard", () => {
  it("shows the run, the best and a dot per day of the last 35", () => {
    render(<StreakCard streak={{ current: 5, best: 15, days }} />);
    expect(screen.getByRole("heading", { name: "Study streak" })).toBeInTheDocument();
    expect(screen.getByText("5 days")).toBeInTheDocument();
    expect(screen.getByText("Best: 15 days")).toBeInTheDocument();
    const dots = screen.getByRole("img", { name: "Studied on 9 of the last 35 days" });
    expect(dots.children).toHaveLength(35);
    const marks = [...dots.children].map((d) => d.getAttribute("data-day"));
    expect(marks.slice(0, 21).every((m) => m === "none")).toBe(true);
    expect(marks.slice(21)).toEqual([
      "studied", "none", "studied", "studied", "none", "studied", "none",
      "none", "none", "run", "run", "run", "run", "run",
    ]);
    expect(dots.children[34]).toHaveAttribute("data-today");
  });

  it("keeps a run that ends yesterday", () => {
    const yesterday = [...days.slice(0, 34), false];
    render(<StreakCard streak={{ current: 4, best: 4, days: yesterday }} />);
    const marks = [...screen.getByRole("img").children].map((d) => d.getAttribute("data-day"));
    expect(marks.slice(30)).toEqual(["run", "run", "run", "run", "none"]);
    expect(screen.getByText("Best: 4 days")).toBeInTheDocument();
  });

  it("says one day, not one days", () => {
    render(<StreakCard streak={{ current: 1, best: 1, days: [...Array(34).fill(false), true] }} />);
    expect(screen.getByText("1 day")).toBeInTheDocument();
    expect(screen.getByText("Best: 1 day")).toBeInTheDocument();
  });

  it("at 375 px is a small tile without the dots", () => {
    render(<StreakCard streak={{ current: 12, best: 15, days }} compact />);
    expect(screen.getByText("Streak")).toBeInTheDocument();
    expect(screen.getByText("12 days")).toBeInTheDocument();
    expect(screen.getByText("Best: 15")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
