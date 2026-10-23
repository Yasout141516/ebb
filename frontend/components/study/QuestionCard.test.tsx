import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { QuestionCard as Model } from "@/lib/session";
import QuestionCard from "./QuestionCard";

const mcq = (over: Partial<Model> = {}): Model => ({
  kind: "question",
  id: "q1",
  concept: "Subnetting",
  number: 2,
  question: {
    type: "mcq",
    difficulty: "medium",
    prompt: "How many usable host addresses does a /26 have?",
    options: ["64", "62", "30", "126"],
    source: "notes",
    source_line: "From your notes · p. 21",
  },
  answer: null,
  result: null,
  ...over,
});

describe("QuestionCard", () => {
  it("labels the question and shows its source line", () => {
    render(<QuestionCard card={mcq()} active onChoose={() => {}} />);
    expect(screen.getByText("Multiple choice · Subnetting · Q2")).toBeInTheDocument();
    expect(screen.getByText("From your notes · p. 21")).toBeInTheDocument();
  });

  it("chooses with keys 1–4 and submits with Enter", async () => {
    const onChoose = vi.fn();
    render(<QuestionCard card={mcq()} active onChoose={onChoose} />);
    const check = screen.getByRole("button", { name: "Check answer" });
    expect(check).toBeDisabled();
    await userEvent.keyboard("3");
    expect(screen.getByRole("radio", { name: /30/ })).toBeChecked();
    await userEvent.keyboard("2");
    expect(screen.getByRole("radio", { name: /62/ })).toBeChecked();
    expect(check).toBeEnabled();
    await userEvent.keyboard("{Enter}");
    expect(onChoose).toHaveBeenCalledWith(1);
  });

  it("submits with Check answer", async () => {
    const onChoose = vi.fn();
    render(<QuestionCard card={mcq()} active onChoose={onChoose} />);
    await userEvent.click(screen.getByRole("radio", { name: /126/ }));
    await userEvent.click(screen.getByRole("button", { name: "Check answer" }));
    expect(onChoose).toHaveBeenCalledWith(3);
  });

  it("ignores keys while it isn't the waiting question", async () => {
    const onChoose = vi.fn();
    render(<QuestionCard card={mcq()} active={false} onChoose={onChoose} />);
    await userEvent.keyboard("1{Enter}");
    expect(screen.getByRole("radio", { name: /64/ })).not.toBeChecked();
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("locks an answered question and marks the learner's answer", () => {
    render(
      <QuestionCard
        card={mcq({ answer: { attempt_id: "a1", text: "62", choice: 1 }, result: "correct" })}
        active={false}
        onChoose={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: /62/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /62/ })).toBeDisabled();
    expect(screen.getByText("Your answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check answer" })).not.toBeInTheDocument();
  });

  it("shows a short-answer question without options", () => {
    const question = { ...mcq().question, type: "short" as const, options: [], source_line: null };
    render(<QuestionCard card={mcq({ question })} active onChoose={() => {}} />);
    expect(screen.getByText("Short answer · Subnetting · Q2")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
