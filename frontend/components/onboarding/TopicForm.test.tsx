import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, postJson } from "@/lib/api";
import TopicForm from "./TopicForm";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  postJson: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

const start = () => screen.getByRole("button", { name: /Start studying|Mapping the topic/ });

describe("TopicForm", () => {
  it("disables Start studying until a topic is typed", async () => {
    const user = userEvent.setup();
    render(<TopicForm />);
    expect(start()).toBeDisabled();
    await user.type(screen.getByLabelText("Topic"), "Operating Systems");
    expect(start()).toBeEnabled();
  });

  it("maps the topic and opens the workspace on 201", async () => {
    let created: (value: unknown) => void = () => {};
    vi.mocked(postJson).mockReturnValue(new Promise((resolve) => (created = resolve)));
    const user = userEvent.setup();
    render(<TopicForm />);
    await user.type(screen.getByLabelText("Topic"), " Linear Algebra ");
    await user.click(start());
    expect(postJson).toHaveBeenCalledWith("/subjects", { topic: "Linear Algebra" });
    expect(screen.getByText("Mapping the topic…")).toBeInTheDocument();
    expect(start()).toBeDisabled();
    created({ id: "s9", name: "Linear Algebra", concepts: [] });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/study/s9"));
  });

  it("fills the topic from a suggestion", async () => {
    const user = userEvent.setup();
    render(<TopicForm />);
    await user.click(screen.getByRole("button", { name: "Operating Systems" }));
    expect(screen.getByLabelText("Topic")).toHaveValue("Operating Systems");
  });

  it("shows the API's copy when the name is taken", async () => {
    const copy = "Linear Algebra already exists — add notes to it from its Sources list.";
    vi.mocked(postJson).mockRejectedValue(new ApiError(409, copy));
    const user = userEvent.setup();
    render(<TopicForm />);
    await user.type(screen.getByLabelText("Topic"), "linear algebra");
    await user.click(start());
    expect(await screen.findByText(copy)).toBeInTheDocument();
    expect(start()).toHaveTextContent("Start studying");
    expect(push).not.toHaveBeenCalled();
  });
});
