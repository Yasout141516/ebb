import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { GradeCard as Model } from "@/lib/session";
import type { Citation, Grade } from "@/lib/types";
import GradeCard from "./GradeCard";

const cite = (over: Partial<Citation>): Citation => ({
  kind: "chunk",
  ref: "c1",
  label: "CSE321_Wk1-6.pdf · p. 12",
  page: 12,
  quote: "A recursive resolver caches each answer it receives.",
  name: "CSE321_Wk1-6.pdf",
  heading: null,
  title: null,
  ...over,
});

const grade = (over: Partial<Grade> = {}): Grade => ({
  value: "partial",
  explanation: "You have the purpose right.",
  misconception: null,
  claims: [],
  citations: [],
  general_knowledge: false,
  ...over,
});

const card = (over: Partial<Model> = {}): Model => ({
  kind: "grade",
  id: "g1",
  grade: grade(),
  canDispute: false,
  dispute: null,
  ...over,
});

describe("GradeCard", () => {
  it("numbers notes then web chips 1..n and never shows an empty chip", () => {
    const citations = [
      cite({ kind: "web", ref: "w1", label: "cloudflare.com · What is DNS?", quote: "Resolvers cache." }),
      cite({ ref: "c1" }),
      cite({ ref: "c2", label: "", quote: "No label." }),
      cite({ ref: "c3", label: "networks.md · § TTL", page: null, quote: "  " }),
      cite({ ref: "c4", label: "networks.md · § TTL", page: null, quote: "Each record carries a TTL." }),
    ];
    render(<GradeCard card={card({ grade: grade({ citations }) })} onDispute={() => {}} />);
    const chips = screen.getAllByRole("button", { name: /^\[\d+\]$/ });
    expect(chips.map((c) => c.textContent)).toEqual(["[1]", "[2]", "[3]"]);
    expect(chips[0]).toHaveAccessibleDescription("From your notes · CSE321_Wk1-6.pdf · p. 12");
    expect(chips[2]).toHaveAccessibleDescription("From the web · cloudflare.com · What is DNS?");
  });

  it("opens a popover with the label and the quote, and no link", async () => {
    render(<GradeCard card={card({ grade: grade({ citations: [cite({})] }) })} onDispute={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "[1]" }));
    const popover = screen.getByRole("dialog", { name: "Source snippet" });
    expect(within(popover).getByText("CSE321_Wk1-6.pdf · p. 12")).toBeInTheDocument();
    expect(popover).toHaveTextContent("“A recursive resolver caches each answer it receives.”");
    expect(within(popover).queryByRole("link")).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("labels general knowledge instead of sources", () => {
    render(<GradeCard card={card({ grade: grade({ general_knowledge: true }) })} onDispute={() => {}} />);
    expect(screen.getByText("General knowledge · not from your notes")).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
  });

  it("shows the verdict and offers Dispute grade only when it can be disputed", async () => {
    const onDispute = vi.fn();
    const { rerender } = render(<GradeCard card={card()} onDispute={onDispute} />);
    expect(screen.getByRole("article", { name: "Grade: Partial" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dispute grade" })).not.toBeInTheDocument();
    rerender(<GradeCard card={card({ canDispute: true })} onDispute={onDispute} />);
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    expect(onDispute).toHaveBeenCalledWith("g1");
  });

  it("shows the re-check while it runs and its result after", () => {
    const { rerender } = render(<GradeCard card={card({ dispute: "checking" })} onDispute={() => {}} />);
    expect(screen.getByText("Dispute sent. A second grader is re-checking your answer…")).toBeInTheDocument();
    rerender(
      <GradeCard
        card={card({
          dispute: {
            outcome: "changed",
            original_grade: "partial",
            new_grade: "correct",
            explanation: "",
            citations: [cite({})],
            concept: "Subnetting",
            stage: 3,
          },
        })}
        onDispute={() => {}}
      />,
    );
    const status = screen.getByText(/Re-check: raised to Correct/).closest("p")!;
    expect(status).toHaveTextContent(
      "Re-check complete. Re-check: raised to Correct — Subnetting back to ●●●○○ level 3.",
    );
    expect(screen.getByRole("article", { name: "Grade: Correct" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "[1]" })).toHaveLength(1);
  });

  it("words every re-check outcome as the CLI does", () => {
    const { rerender } = render(<GradeCard card={card({ dispute: { outcome: "refused" } })} onDispute={() => {}} />);
    expect(screen.getByText("Only short-answer grades from this session can be disputed.")).toBeInTheDocument();
    expect(screen.queryByText("Re-check complete.")).not.toBeInTheDocument();
    rerender(<GradeCard card={card({ dispute: { outcome: "failed" } })} onDispute={() => {}} />);
    expect(screen.getByText("Couldn't re-check right now — the grade stands. Try again with d.")).toBeInTheDocument();
    const base = { original_grade: "correct" as const, citations: [], concept: "DNS", stage: 1 };
    rerender(
      <GradeCard
        card={card({ dispute: { ...base, outcome: "voided", new_grade: null, explanation: "" } })}
        onDispute={() => {}}
      />,
    );
    expect(screen.getByText(/Re-check: the question was ambiguous — that answer no longer counts\./)).toBeInTheDocument();
    rerender(
      <GradeCard
        card={card({ dispute: { ...base, outcome: "upheld", new_grade: null, explanation: "TTL is set by the zone." } })}
        onDispute={() => {}}
      />,
    );
    expect(screen.getByText(/Re-check: the grade stands — TTL is set by the zone\./)).toBeInTheDocument();
    rerender(
      <GradeCard
        card={card({ dispute: { ...base, outcome: "changed", new_grade: "partial", explanation: "" } })}
        onDispute={() => {}}
      />,
    );
    expect(screen.getByText(/Re-check: lowered to Partial — DNS now ●○○○○ level 1\./)).toBeInTheDocument();
  });
});
