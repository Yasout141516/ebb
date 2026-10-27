import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Composer from "./Composer";

describe("Composer", () => {
  it("keeps Send disabled for blank text", async () => {
    render(<Composer enabled onSend={() => {}} />);
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your answer"), "   ");
    expect(send).toBeDisabled();
  });

  it("sends with Enter and keeps Shift+Enter for a new line", async () => {
    const onSend = vi.fn();
    render(<Composer enabled onSend={onSend} />);
    const box = screen.getByLabelText("Your answer");
    await userEvent.type(box, "Caching saves time.{Shift>}{Enter}{/Shift}TTL decides.");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("Caching saves time.\nTTL decides.");
    expect(box).toHaveValue("");
  });

  it("ignores Enter while an input method is composing", () => {
    const onSend = vi.fn();
    render(<Composer enabled onSend={onSend} />);
    const box = screen.getByLabelText("Your answer");
    fireEvent.change(box, { target: { value: "にほん" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("にほん");
  });

  it("sends with the button, trimmed", async () => {
    const onSend = vi.fn();
    render(<Composer enabled onSend={onSend} />);
    await userEvent.type(screen.getByLabelText("Your answer"), "  62  ");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("62");
  });

  it("is disabled while no short-answer question waits", async () => {
    const onSend = vi.fn();
    render(<Composer enabled={false} onSend={onSend} />);
    expect(screen.getByLabelText("Your answer")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
