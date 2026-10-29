import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TokensPerDay from "./TokensPerDay";

describe("TokensPerDay", () => {
  it("draws no bar for a day whose tokens were not reported and says so", () => {
    const days = [
      { day: "2026-10-27", input_tokens: null, output_tokens: null, tokens: null, calls: 2 },
      { day: "2026-10-28", input_tokens: 400, output_tokens: 100, tokens: 500, calls: 3 },
    ];
    const { container } = render(<TokensPerDay days={days} anomalies={[]} />);
    expect(screen.getByRole("img", { name: "Tokens per day: 27 Oct not reported, 28 Oct 500" })).toBeInTheDocument();
    const bars = [...container.querySelectorAll<HTMLElement>("[class*=bar]")];
    expect(bars.map((b) => b.style.height)).toEqual(["0px", "150px"]);
  });
});
