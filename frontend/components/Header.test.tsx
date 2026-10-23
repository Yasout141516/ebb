import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Header from "./Header";

vi.mock("next/navigation", () => ({ usePathname: () => "/study/1" }));

describe("Header", () => {
  beforeEach(() => {
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

  it("names the backup model", () => {
    render(<Header backup />);
    expect(screen.getByText("Tutor ready · backup model")).toBeInTheDocument();
  });

  it("toggles data-theme and remembers it", async () => {
    render(<Header backup={false} />);
    await userEvent.click(screen.getByRole("button", { name: /switch to/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("ebb-theme")).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: /switch to/i }));
    expect(document.documentElement.dataset.theme).toBe("light");
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
