import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ConceptState, Concepts } from "@/lib/types";
import MemoryPanel, { dueText, MemoryPeek, MemorySheetBody, nextGap } from "./MemoryPanel";

const NOW = new Date(2026, 9, 23, 12, 0); // Fri 23 Oct 2026, local time

const concept = (over: Partial<ConceptState>): ConceptState => ({
  id: over.name ?? "c",
  name: "Concept",
  description: "",
  stage: 1,
  tide: 0.5,
  band: "low tide",
  next_review_at: new Date(2026, 9, 30, 9, 0).toISOString(),
  due: false,
  projected_tide: 0.4,
  ...over,
});

const data: Concepts = {
  concepts: [
    concept({ name: "Subnetting", stage: 1, tide: 0.29, band: "low tide", due: true, projected_tide: 0.17, next_review_at: new Date(2026, 9, 21, 9, 0).toISOString() }),
    concept({ name: "NAT", stage: 0, tide: null, band: "new", next_review_at: null, projected_tide: null }),
    concept({ name: "OSI layers", stage: 5, tide: 0.92, band: "high tide", projected_tide: 0.86 }),
    concept({ name: "HTTP vs HTTPS", stage: 3, tide: 0.71, band: "ebbing", projected_tide: 0.58 }),
    concept({ name: "Routing", stage: 2, tide: 0.64, band: "ebbing", due: true, next_review_at: new Date(2026, 9, 23, 8, 0).toISOString() }),
  ],
  due_count: 2,
  next_review_at: new Date(2026, 9, 21, 9, 0).toISOString(),
};

describe("MemoryPanel", () => {
  it("lists due concepts, lowest tide first, with their tide and how long they've been due", () => {
    render(<MemoryPanel concepts={data} now={NOW} />);
    const due = screen.getByRole("region", { name: "Due for review · 2" });
    const rows = within(due).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "SubnettingTide 29% · due 2 days ago",
      "RoutingTide 64% · due today",
    ]);
    // Review buttons led to Flashcards: they're not rendered.
    expect(within(due).queryByRole("link")).toBeNull();
    expect(within(due).queryByRole("button")).toBeNull();
  });

  it("shows every concept's tide, band and stage dots, highest tide first, new ones last", () => {
    render(<MemoryPanel concepts={data} now={NOW} />);
    const all = screen.getByRole("region", { name: "All concepts" });
    const rows = within(all).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual([
      "OSI layers: tide 92 percent, high tide, level 5 of 5, next gap 35 days, about 86 percent in 3 days without review",
      "HTTP vs HTTPS: tide 71 percent, ebbing, level 3 of 5, next gap 16 days, about 58 percent in 3 days without review",
      "Routing: tide 64 percent, ebbing, level 2 of 5, next gap 7 days, about 40 percent in 3 days without review",
      "Subnetting: tide 29 percent, low tide, level 1 of 5, next gap 3 days, about 17 percent in 3 days without review",
      "NAT: new, not studied yet",
    ]);
    const http = rows[1];
    expect(within(http).getByText("71%")).toBeInTheDocument();
    expect(within(http).getByText("ebbing")).toBeInTheDocument();
    expect(within(http).getByText("level 3 · next gap 16 days")).toBeInTheDocument();
    const dots = http.querySelectorAll("[data-dot]");
    expect([...dots].map((d) => d.getAttribute("data-dot")).join("")).toBe("●●●○○");
    expect(within(rows[4]).getByText("new")).toBeInTheDocument();
    expect(within(rows[4]).getByText("No reviews yet")).toBeInTheDocument();
  });

  it("explains tide and stage in one-line tooltips", () => {
    render(<MemoryPanel concepts={data} now={NOW} />);
    const row = screen.getAllByRole("listitem").find((r) => r.textContent?.startsWith("HTTP vs HTTPS"))!;
    expect(within(row).getByText("71%").closest("[title]")?.getAttribute("title")).toMatch(/^Tide: /);
    expect(within(row).getByText("level 3 · next gap 16 days").closest("[title]")?.getAttribute("title")).toMatch(
      /^Stage: /,
    );
  });

  it("draws the 3-day projection marker only for studied concepts, and says what it is", () => {
    render(<MemoryPanel concepts={data} now={NOW} />);
    expect(document.querySelectorAll("[data-projection]")).toHaveLength(4);
    expect(screen.getByText("Where the tide will be in 3 days without review")).toBeInTheDocument();
  });

  it("with nothing due says when the next review is, and offers no quiz ahead", () => {
    const calm: Concepts = {
      concepts: [concept({ name: "OSI layers", stage: 5, tide: 0.92, band: "high tide" })],
      due_count: 0,
      next_review_at: new Date(2026, 9, 29, 9, 0).toISOString(), // a Thursday
    };
    render(<MemoryPanel concepts={calm} now={NOW} />);
    expect(screen.getByText("Tide's high. Next review Thursday.")).toBeInTheDocument();
    expect(screen.queryByText(/quiz ahead/i)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the loading skeleton until the levels arrive", () => {
    render(<MemoryPanel concepts={null} now={NOW} />);
    expect(screen.getByLabelText("Loading memory levels")).toHaveAttribute("aria-busy", "true");
  });

  it("the sheet's peek shows a gauge per concept and the due count", () => {
    render(<MemoryPeek concepts={data} />);
    expect(screen.getByText("2 due")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-mini]")).toHaveLength(5);
  });

  it("the sheet lists due concepts and every concept, and links to the dashboard", () => {
    render(<MemorySheetBody concepts={data} now={NOW} />);
    expect(screen.getByRole("heading", { name: "Due for review · 2" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open full progress" })).toHaveAttribute("href", "/progress");
    expect(screen.getAllByRole("listitem").map((r) => r.getAttribute("aria-label"))).toContain(
      "HTTP vs HTTPS: tide 71 percent, ebbing, level 3 of 5, next gap 16 days, about 58 percent in 3 days without review",
    );
  });
});

describe("memory wording", () => {
  it("nextGap is the review gap a correct answer at this stage leads to", () => {
    expect([1, 2, 3, 4, 5].map(nextGap)).toEqual([3, 7, 16, 35, 35]);
  });

  it("dueText counts calendar days", () => {
    expect(dueText(null, NOW)).toBe("due today");
    expect(dueText(new Date(2026, 9, 23, 1, 0).toISOString(), NOW)).toBe("due today");
    expect(dueText(new Date(2026, 9, 22, 23, 0).toISOString(), NOW)).toBe("due yesterday");
    expect(dueText(new Date(2026, 9, 20, 23, 0).toISOString(), NOW)).toBe("due 3 days ago");
  });
});
