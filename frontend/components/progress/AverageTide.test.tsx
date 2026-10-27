import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Average } from "@/lib/types";
import AverageTide, { changeText, footnote } from "./AverageTide";

const average = (over: Partial<Average> = {}): Average => ({
  now: 0.62,
  change: 0.09,
  since: "2026-09-23",
  counted: 7,
  not_counted: ["NAT"],
  ...over,
});

describe("AverageTide", () => {
  it("shows the level, the change since 30 days ago and who is counted", () => {
    render(<AverageTide average={average()} />);
    expect(screen.getByRole("heading", { name: "Average tide" })).toBeInTheDocument();
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByText("+9 points since 23 Sep")).toBeInTheDocument();
    expect(
      screen.getByText("Across 7 studied concepts. NAT isn't counted until its first review."),
    ).toBeInTheDocument();
  });

  it("writes a fall with a minus sign", () => {
    render(<AverageTide average={average({ change: -0.031 })} />);
    expect(screen.getByText("−3 points since 23 Sep")).toBeInTheDocument();
  });

  it("leaves the change out when there is nothing to compare with", () => {
    render(<AverageTide average={average({ change: null })} />);
    expect(screen.queryByText(/since/)).not.toBeInTheDocument();
  });

  it("leaves the footnote out when there is nothing to say", () => {
    const { container } = render(<AverageTide average={average({ now: null, change: null, counted: 0, not_counted: [] })} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toEqual(["—"]);
  });

  it("at 375 px is a small tile with the short change", () => {
    render(<AverageTide average={average()} compact />);
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByText("+9 since 23 Sep")).toBeInTheDocument();
  });

  it("changeText reads the date as a calendar day", () => {
    expect(changeText(0.09, "2026-09-23")).toBe("+9 points since 23 Sep");
    expect(changeText(-0.01, "2026-01-01")).toBe("−1 point since 1 Jan");
    expect(changeText(0.001, "2026-12-31", true)).toBe("0 since 31 Dec");
  });

  it("footnote names who isn't counted yet", () => {
    expect(footnote(1, [])).toBe("Across 1 studied concept.");
    expect(footnote(2, ["NAT", "ARP"])).toBe("Across 2 studied concepts. NAT and ARP aren't counted until their first review.");
    expect(footnote(0, ["A", "B", "C", "D", "E"])).toBe("A, B and 3 more aren't counted until their first review.");
    expect(footnote(3, ["A", "B", "C"])).toBe("Across 3 studied concepts. A, B and C aren't counted until their first review.");
  });
});
