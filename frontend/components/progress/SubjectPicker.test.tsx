import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SubjectPicker from "./SubjectPicker";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const subjects = [
  { id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 },
  { id: "s2", name: "Linear Algebra", concept_count: 5, due_count: 0 },
];

describe("SubjectPicker", () => {
  it("shows the current subject and opens another's progress", async () => {
    render(<SubjectPicker subjects={subjects} current="s1" />);
    const picker = screen.getByRole("combobox", { name: "Subject" });
    expect(picker).toHaveValue("s1");
    await userEvent.selectOptions(picker, "Linear Algebra");
    expect(push).toHaveBeenCalledWith("/progress/s2");
  });
});
