import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Exchange } from "@/lib/types";
import LearnExchange, { SuggestedNext } from "./LearnExchange";

const exchange: Exchange = {
  position: 0,
  kind: "typed",
  question: "What if the SYN-ACK is lost?",
  answer: "The client sends its **SYN** again after a timeout.",
  citations: [],
  suggestions: [],
  asked_at: "2026-10-31T09:01:00Z",
};

describe("LearnExchange", () => {
  it("shows the question as the learner's, then Ebb's answer, marked when not from the notes", () => {
    render(<LearnExchange question={exchange.question} exchange={exchange} hasNotes />);
    expect(screen.getByText("What if the SYN-ACK is lost?")).toBeInTheDocument();
    expect(screen.getByText("SYN", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Not in your notes")).toBeInTheDocument();
  });

  it("has no mark when the subject has no notes", () => {
    render(<LearnExchange question={exchange.question} exchange={exchange} hasNotes={false} />);
    expect(screen.queryByText("Not in your notes")).not.toBeInTheDocument();
  });

  it("shows the answer streaming in, in a live region", () => {
    render(<LearnExchange question={exchange.question} exchange={null} live="The client sends" hasNotes />);
    const live = screen.getByText("The client sends").closest("[aria-live]");
    expect(live).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("Not in your notes")).not.toBeInTheDocument();
  });
});

describe("SuggestedNext", () => {
  it("asks a suggestion when tapped", async () => {
    const onAsk = vi.fn();
    render(<SuggestedNext suggestions={["How do SYN cookies work?", "What does TLS add?"]} onAsk={onAsk} />);
    expect(screen.getByText("Suggested next")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "What does TLS add?" }));
    expect(onAsk).toHaveBeenCalledWith("What does TLS add?");
  });

  it("shows nothing without suggestions", () => {
    const { container } = render(<SuggestedNext suggestions={[]} onAsk={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
