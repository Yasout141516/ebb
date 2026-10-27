import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StreakCard from "./StreakCard";

// Two weeks, oldest first: studied on 9 days, the last 5 of them in a row up to today.
const last14 = [true, false, true, true, false, true, false, false, false, true, true, true, true, true];

describe("StreakCard", () => {
  it("shows the run, the best and a dot per day of the last two weeks", () => {
    render(<StreakCard streak={{ current: 5, best: 15, last14 }} />);
    expect(screen.getByRole("heading", { name: "Study streak" })).toBeInTheDocument();
    expect(screen.getByText("5 days")).toBeInTheDocument();
    expect(screen.getByText("Best: 15 days")).toBeInTheDocument();
    const dots = screen.getByRole("img", { name: "Studied on 9 of the last 14 days" });
    const marks = [...dots.children].map((d) => d.getAttribute("data-day"));
    expect(marks).toEqual([
      "studied", "none", "studied", "studied", "none", "studied", "none",
      "none", "none", "run", "run", "run", "run", "run",
    ]);
    expect(dots.children[13]).toHaveAttribute("data-today");
  });

  it("keeps a run that ends yesterday", () => {
    const yesterday = [...last14.slice(0, 13), false];
    render(<StreakCard streak={{ current: 4, best: 4, last14: yesterday }} />);
    const marks = [...screen.getByRole("img").children].map((d) => d.getAttribute("data-day"));
    expect(marks.slice(9)).toEqual(["run", "run", "run", "run", "none"]);
    expect(screen.getByText("Best: 4 days")).toBeInTheDocument();
  });

  it("says one day, not one days", () => {
    render(<StreakCard streak={{ current: 1, best: 1, last14: [...Array(13).fill(false), true] }} />);
    expect(screen.getByText("1 day")).toBeInTheDocument();
    expect(screen.getByText("Best: 1 day")).toBeInTheDocument();
  });

  it("at 375 px is a small tile without the dots", () => {
    render(<StreakCard streak={{ current: 12, best: 15, last14 }} compact />);
    expect(screen.getByText("Streak")).toBeInTheDocument();
    expect(screen.getByText("12 days")).toBeInTheDocument();
    expect(screen.getByText("Best: 15")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
