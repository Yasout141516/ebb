import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getJson } from "@/lib/api";
import Header, { ThemeToggle } from "./Header";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
}));

const health = (costs: boolean) => ({ database: "ok", tutor: "ok", timezone: "ok", costs });

vi.mock("next/navigation", () => ({ usePathname: () => "/study/1" }));

describe("Header", () => {
  beforeEach(() => {
    vi.mocked(getJson).mockResolvedValue(health(false));
    document.documentElement.removeAttribute("data-theme");
    window.localStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the links and the status pill", () => {
    render(<Header backup={false} />);
    expect(screen.getByRole("link", { name: "Study" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Progress" })).toBeInTheDocument();
    expect(screen.getByText("Tutor ready")).toBeInTheDocument();
  });

  it("links to the current subject's study and progress pages", () => {
    render(<Header backup={false} />);
    expect(screen.getByRole("link", { name: "Study" })).toHaveAttribute("href", "/study/1");
    expect(screen.getByRole("link", { name: "Study" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Progress" })).toHaveAttribute("href", "/progress/1");
    expect(screen.getByRole("link", { name: "Progress" })).not.toHaveAttribute("aria-current");
  });

  it("shows Costs with a dev-only tag when health.costs is true", async () => {
    vi.mocked(getJson).mockResolvedValue(health(true));
    render(<Header />);
    const link = await screen.findByRole("link", { name: /Costs/ });
    expect(link).toHaveAttribute("href", "/costs"); // no subject id, unlike Study and Progress
    expect(link).toHaveTextContent("dev only");
  });

  it("hides Costs when health.costs is false", async () => {
    render(<Header />);
    await vi.waitFor(() => expect(getJson).toHaveBeenCalledWith("/health", expect.anything()));
    expect(screen.queryByRole("link", { name: /Costs/ })).not.toBeInTheDocument();
  });

  it("hides Costs when /health can't be reached", async () => {
    vi.mocked(getJson).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<Header />);
    await vi.waitFor(() => expect(getJson).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: /Costs/ })).not.toBeInTheDocument();
  });

  it("names the backup model", () => {
    render(<Header backup />);
    expect(screen.getByText("Tutor ready · backup model")).toBeInTheDocument();
  });

  it("shows only the mark and the toggle when minimal", () => {
    render(<Header minimal />);
    expect(screen.getByText("Ebb")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /switch to/i })).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("toggles data-theme and remembers it", async () => {
    render(<Header backup={false} />);
    await userEvent.click(screen.getByRole("button", { name: /switch to/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("ebb-theme")).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: /switch to/i }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("keeps a second toggle (the 375 px bar's) in step", async () => {
    render(
      <>
        <Header backup={false} />
        <ThemeToggle plain />
      </>,
    );
    const [header, bar] = screen.getAllByRole("button", { name: "Switch to dark mode" });
    await userEvent.click(bar);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(header).toHaveAccessibleName("Switch to light mode");
  });

  it("survives a throwing localStorage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<Header backup={false} />);
    await userEvent.click(screen.getByRole("button", { name: /switch to/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
