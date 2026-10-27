import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NewSubject from "@/app/new/page";
import { getJson } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/new",
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getJson).mockImplementation(async (path: string) =>
    path === "/health" ? { database: "ok", tutor: "ok", timezone: "ok" } : [],
  );
});

describe("the onboarding page", () => {
  it("has a minimal header: the mark and the toggle, no nav", async () => {
    render(<NewSubject />);
    expect(screen.getByText("Ebb")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Study" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /switch to/i })).toBeInTheDocument();
  });

  it("opens on Upload notes and moves between tabs with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<NewSubject />);
    const upload = screen.getByRole("tab", { name: "Upload notes" });
    const topic = screen.getByRole("tab", { name: "Type a topic" });
    expect(upload).toHaveAttribute("aria-selected", "true");
    expect(upload).toHaveAttribute("tabindex", "0");
    expect(topic).toHaveAttribute("tabindex", "-1");
    expect(await screen.findByLabelText("Subject name")).toBeVisible();

    upload.focus();
    await user.keyboard("{ArrowRight}");
    expect(topic).toHaveFocus();
    expect(topic).toHaveAttribute("aria-selected", "true");
    expect(topic).toHaveAttribute("tabindex", "0");
    expect(screen.getByLabelText("Topic")).toBeVisible();
    expect(screen.getByLabelText("Subject name")).not.toBeVisible();

    await user.keyboard("{ArrowLeft}");
    expect(upload).toHaveFocus();
    expect(upload).toHaveAttribute("aria-selected", "true");
  });
});
