import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import BottomSheet from "./BottomSheet";
import DisputeDialog from "./DisputeDialog";

function Opener({ onSend = vi.fn() }: { onSend?: (reason: string, explanation: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Dispute grade
      </button>
      {open && (
        <DisputeDialog
          context="Short answer on DNS resolution, graded Partial. A second grader will re-check it against your notes."
          onCancel={() => setOpen(false)}
          onSend={(reason, explanation) => {
            setOpen(false);
            onSend(reason, explanation);
          }}
        />
      )}
    </>
  );
}

describe("DisputeDialog", () => {
  it("offers the four reasons, the first chosen and focused", async () => {
    render(<Opener />);
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    const dialog = screen.getByRole("dialog", { name: "Dispute this grade" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("group", { name: "What's wrong with it?" })).toBeInTheDocument();
    const reasons = screen.getAllByRole("radio");
    expect(reasons.map((r) => r.closest("label")?.textContent)).toEqual([
      "My answer is correct",
      "The question is ambiguous",
      "My notes say something different",
      "Something else",
    ]);
    expect(reasons[0]).toBeChecked();
    expect(reasons[0]).toHaveFocus();
    expect(screen.getByLabelText("Explain (optional)")).toBeInTheDocument();
  });

  it("sends the chosen reason's API value and the explanation", async () => {
    const onSend = vi.fn();
    render(<Opener onSend={onSend} />);
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    await userEvent.click(screen.getByLabelText("My notes say something different"));
    await userEvent.type(screen.getByLabelText("Explain (optional)"), "Page 14 says so.");
    await userEvent.click(screen.getByRole("button", { name: "Send for re-check" }));
    expect(onSend).toHaveBeenCalledWith("notes_differ", "Page 14 says so.");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Cancel and Escape close it and give focus back to the button that opened it", async () => {
    render(<Opener />);
    const opener = screen.getByRole("button", { name: "Dispute grade" });
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();

    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("keeps Tab inside the dialog", async () => {
    render(<Opener />);
    await userEvent.click(screen.getByRole("button", { name: "Dispute grade" }));
    const send = screen.getByRole("button", { name: "Send for re-check" });
    send.focus();
    await userEvent.tab();
    expect(screen.getAllByRole("radio")[0]).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(send).toHaveFocus();
  });
});

describe("BottomSheet", () => {
  it("opens from its peek, closes with Escape or ✕, and gives focus back", async () => {
    render(
      <BottomSheet title="Memory" peek={<span>2 due</span>}>
        <p>Sheet body</p>
      </BottomSheet>,
    );
    const peek = screen.getByRole("button", { name: /Memory/ });
    expect(peek).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Sheet body")).toBeNull();

    await userEvent.click(peek);
    expect(screen.getByRole("dialog", { name: "Memory" })).toBeInTheDocument();
    expect(screen.getByText("Sheet body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close memory panel" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(peek).toHaveFocus();

    await userEvent.click(peek);
    await userEvent.click(screen.getByRole("button", { name: "Close memory panel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
