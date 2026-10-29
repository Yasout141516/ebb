import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Filters from "./Filters";

describe("Filters", () => {
  it("lists a model id once even when two providers serve it, with no key warning", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const row = (key: string) => ({ key, label: key }) as never;
    render(
      <Filters
        values={{ range: "7d", tenant: "", endpoint: "", model: "" }}
        tenants={[]}
        endpoints={[]}
        models={[row("groq/gpt-oss"), row("openrouter/gpt-oss"), row("mistral/small")]}
        onChange={() => {}}
      />,
    );
    const options = screen.getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(options.filter((v) => v === "gpt-oss")).toHaveLength(1);
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});
