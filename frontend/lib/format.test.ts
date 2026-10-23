import { describe, expect, it } from "vitest";
import { stageDots, tidePercent, weekday } from "./format";

describe("format", () => {
  it("tidePercent", () => {
    expect(tidePercent(null)).toBe("new");
    expect(tidePercent(0.644)).toBe("64%");
    expect(tidePercent(1)).toBe("100%");
    expect(tidePercent(0)).toBe("0%");
  });

  it("stageDots", () => {
    expect(stageDots(3)).toBe("●●●○○");
    expect(stageDots(0)).toBe("○○○○○");
    expect(stageDots(5)).toBe("●●●●●");
    expect(stageDots(9)).toBe("●●●●●");
  });

  it("weekday uses the given zone", () => {
    expect(weekday("2026-10-23T23:30:00Z", "UTC")).toBe("Fri");
    expect(weekday("2026-10-23T23:30:00Z", "Asia/Tokyo")).toBe("Sat");
  });
});
