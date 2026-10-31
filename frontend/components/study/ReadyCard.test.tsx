import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ReadyCard from "./ReadyCard";

describe("ReadyCard", () => {
  it("asks whether the learner is ready, in the spec's words", async () => {
    const onReady = vi.fn();
    render(<ReadyCard onReady={onReady} />);
    expect(screen.getByRole("heading", { name: "Ready to be quizzed?" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Ebb starts with multiple choice. Questions you asked here do not change your tide or your review schedule.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "I'm ready, quiz me" }));
    expect(onReady).toHaveBeenCalled();
  });
});
