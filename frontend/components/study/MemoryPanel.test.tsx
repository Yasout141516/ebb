import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
    // Each row's text alternative: its name and two tooltip buttons, read as they're shown.
    const expected = [
      ["OSI layers", "level 5 · next gap 35 days", "Tide 92% high tide, about 86% in 3 days without review"],
      ["HTTP vs HTTPS", "level 3 · next gap 16 days", "Tide 71% ebbing, about 58% in 3 days without review"],
      ["Routing", "level 2 · next gap 7 days", "Tide 64% ebbing, about 40% in 3 days without review"],
      ["Subnetting", "level 1 · next gap 3 days", "Tide 29% low tide, about 17% in 3 days without review"],
      ["NAT", "No reviews yet", "Tide not measured yet, new"],
    ];
    expect(rows).toHaveLength(expected.length);
    rows.forEach((row, i) => {
      const [name, stage, tide] = expected[i];
      expect(row.textContent?.startsWith(name)).toBe(true);
      const [stageButton, tideButton] = within(row).getAllByRole("button");
      expect(stageButton).toHaveAccessibleName(stage);
      expect(tideButton).toHaveAccessibleName(tide);
    });
    const http = rows[1];
    expect(within(http).getByText("71%", { exact: false })).toBeInTheDocument();
    expect(within(http).getByText("ebbing")).toBeInTheDocument();
    const dots = http.querySelectorAll("[data-dot]");
    expect([...dots].map((d) => d.getAttribute("data-dot")).join("")).toBe("●●●○○");
    expect(dots[0].closest("[aria-hidden]")).not.toBeNull();
  });

  it("shows each one-line tooltip on hover and on keyboard focus, described by it", async () => {
    render(<MemoryPanel concepts={data} now={NOW} />);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await userEvent.tab();
    const stage = screen.getByRole("button", { name: "level 5 · next gap 35 days" });
    expect(stage).toHaveFocus();
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent(/^Stage: /);
    expect(stage).toHaveAccessibleDescription(tip.textContent!);
    await userEvent.tab();
    expect(screen.getByRole("tooltip")).toHaveTextContent(/^Tide: /);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    await userEvent.hover(screen.getByRole("button", { name: /^Tide 71%/ }));
    expect(screen.getByRole("tooltip")).toHaveTextContent(/^Tide: /);
    await userEvent.unhover(screen.getByRole("button", { name: /^Tide 71%/ }));
    expect(screen.queryByRole("tooltip")).toBeNull();
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
    expect(screen.queryByRole("button", { name: /quiz/i })).toBeNull();
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
    render(<MemorySheetBody concepts={data} now={NOW} subjectId="s1" />);
    expect(screen.getByRole("heading", { name: "Due for review · 2" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open full progress" })).toHaveAttribute("href", "/progress/s1");
    const http = screen.getAllByRole("listitem").find((r) => r.textContent?.includes("HTTP vs HTTPS"))!;
    // The parts are flex items, read as separate words; textContent just runs them together.
    expect(http).toHaveTextContent("HTTP vs HTTPSTide 71% ebbing, about 58% in 3 days without review");
    const due = screen.getAllByRole("listitem").find((r) => r.textContent?.startsWith("Subnetting"))!;
    expect(due).toHaveTextContent("SubnettingTide 29%, due 2 days ago");
  });

  it("the sheet says it's loading, or that loading failed, with Retry", async () => {
    const { rerender } = render(<MemorySheetBody concepts={null} now={NOW} subjectId="s1" />);
    expect(screen.getByText("Loading memory levels…")).toBeInTheDocument();
    const retry = vi.fn();
    rerender(<MemorySheetBody concepts={null} failed onRetry={retry} now={NOW} subjectId="s1" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your memory levels.");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
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
