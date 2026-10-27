import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Chart, ChartConcept } from "@/lib/types";
import TideChart, { reviewCount } from "./TideChart";

const DAY = 86_400_000;
const NOW = Date.parse("2026-10-27T12:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

/** A concept first reviewed 20 days ago (the null-to-tide rise), reviewed again 10 days ago. */
function studied(id: string, name: string, now: number): ChartConcept {
  return {
    id,
    name,
    studied: true,
    now,
    past: [
      [iso(NOW - 30 * DAY), null],
      [iso(NOW - 20 * DAY), 1],
      [iso(NOW - 15 * DAY), 0.6],
      [iso(NOW - 10 * DAY), 0.98],
      [iso(NOW), now],
    ],
    ahead: [
      [iso(NOW + DAY), now - 0.05],
      [iso(NOW + 3 * DAY), now - 0.12],
    ],
  };
}

const nat: ChartConcept = {
  id: "nat",
  name: "NAT",
  studied: false,
  now: null,
  past: [
    [iso(NOW - 30 * DAY), null],
    [iso(NOW), null],
  ],
  ahead: [],
};

const chart = (enough_days: boolean): Chart => ({
  enough_days,
  concepts: [studied("osi", "OSI layers", 0.92), nat, studied("routing", "Routing", 0.44)],
});

describe("TideChart", () => {
  it("hides the chart for a new user and offers a single quiz", () => {
    render(<TideChart chart={chart(false)} subjectId="s1" />);
    expect(
      screen.getByText("Your tide chart fills in as you review — come back after 3 sessions"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a quiz" })).toHaveAttribute("href", "/study/s1");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Choose a concept" })).not.toBeInTheDocument();
  });

  it("starts on the lowest tide and enlarges the concept picked", async () => {
    render(<TideChart chart={chart(true)} subjectId="s1" />);
    const routing = screen.getByRole("button", { name: /^Routing tide/ });
    const osi = screen.getByRole("button", { name: /^OSI layers tide/ });
    expect(routing).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Routing · now 44%")).toBeInTheDocument();

    await userEvent.click(osi);
    expect(osi).toHaveAttribute("aria-pressed", "true");
    expect(routing).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("OSI layers · now 92%")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^OSI layers tide over the last 30 days/ })).toBeInTheDocument();
  });

  it("orders concepts by tide, says what an unstudied one is waiting for, and can't pick it", async () => {
    render(<TideChart chart={chart(true)} subjectId="s1" />);
    const buttons = within(screen.getByRole("group", { name: "Choose a concept" })).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([
      expect.stringContaining("OSI layers92%"),
      expect.stringContaining("Routing44%"),
      expect.stringContaining("NAT—Not studied yet. It joins the queue after its first quiz."),
    ]);
    await userEvent.click(buttons[2]);
    expect(buttons[2]).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Routing · now 44%")).toBeInTheDocument();
  });

  it("describes the line: its reviews, now and in 3 days", () => {
    render(<TideChart chart={chart(true)} subjectId="s1" />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Routing tide over the last 30 days: reviewed 2 times, now 44 percent, about 32 percent in 3 days without review.",
    );
  });

  it("draws the line, the review dots, the dashed 3 days and the axis", () => {
    const { container } = render(<TideChart chart={chart(true)} subjectId="s1" />);
    const big = screen.getByRole("img");
    const [line, ahead] = big.querySelectorAll("polyline");
    // Four measured points (the null is left out), then the projection from now.
    expect(line.getAttribute("points")!.split(" ")).toHaveLength(4);
    expect(ahead.getAttribute("points")!.split(" ")).toHaveLength(3);
    expect(ahead.getAttribute("class")).toMatch(/ahead/);
    expect(big.querySelector("path")!.getAttribute("d")!.match(/M/g)).toHaveLength(2);
    expect(within(big as unknown as HTMLElement).getByText("100%")).toBeInTheDocument();
    expect(within(big as unknown as HTMLElement).getByText("50%")).toBeInTheDocument();
    expect(within(big as unknown as HTMLElement).getByText("0%")).toBeInTheDocument();
    expect(within(big as unknown as HTMLElement).getByText("Today")).toBeInTheDocument();
    expect(container.querySelectorAll("text")).toHaveLength(9); // 3 levels, 5 dates, Today
  });

  it("counts a review at every rise, and none for a line already running", () => {
    expect(reviewCount(studied("x", "X", 0.5))).toBe(2);
    expect(
      reviewCount({
        ...studied("x", "X", 0.5),
        past: [
          [iso(NOW - 30 * DAY), 0.8],
          [iso(NOW), 0.7],
        ],
      }),
    ).toBe(0);
  });

  it("draws a line of one point, or none, without NaN", () => {
    const one: ChartConcept = { ...studied("one", "One", 0.9), past: [[iso(NOW), 0.9]], ahead: [] };
    const none: ChartConcept = { ...studied("none", "None", 0.5), past: [], ahead: [] };
    const { container } = render(
      <TideChart chart={{ enough_days: true, concepts: [one, none] }} subjectId="s1" />,
    );
    render(<TideChart chart={{ enough_days: true, concepts: [one, none] }} subjectId="s1" compact />);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(document.body.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(reviewCount(none)).toBe(0);
  });

  it("at 375 px shows each concept as a small chart instead of picker buttons", () => {
    render(<TideChart chart={chart(true)} subjectId="s1" compact />);
    expect(screen.getByRole("heading", { name: "Tide levels" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "OSI layers: reviewed 2 times in 30 days, now 92 percent" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "NAT: not studied yet" })).toBeInTheDocument();
  });

  it("at 375 px a new user sees the same copy and quiz", () => {
    render(<TideChart chart={chart(false)} subjectId="s1" compact />);
    expect(
      screen.getByText("Your tide chart fills in as you review — come back after 3 sessions"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a quiz" })).toHaveAttribute("href", "/study/s1");
  });
});
