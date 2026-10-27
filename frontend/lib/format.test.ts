import { describe, expect, it } from "vitest";
import {
  calendarDays,
  clockTime,
  day,
  dayMonth,
  formatAgo,
  percent,
  stageDots,
  tidePercent,
  weekday,
} from "./format";

describe("format", () => {
  it("tidePercent", () => {
    expect(tidePercent(null)).toBe("new");
    expect(tidePercent(0.644)).toBe("64%");
    expect(tidePercent(1)).toBe("100%");
    expect(tidePercent(0)).toBe("0%");
    expect(tidePercent(null, "—")).toBe("—");
    expect(percent(0.644)).toBe(64);
    expect(percent(null)).toBe(0);
  });

  it("calendarDays counts local calendar days, not 24-hour spans", () => {
    const late = new Date(2026, 9, 23, 23, 30);
    const early = new Date(2026, 9, 24, 0, 10);
    expect(calendarDays(late, early)).toBe(1);
    expect(calendarDays(early, late)).toBe(-1);
    expect(calendarDays(new Date(2026, 9, 24, 1), new Date(2026, 9, 24, 23))).toBe(0);
    expect(calendarDays(new Date(2026, 9, 20, 12), new Date(2026, 9, 27, 12))).toBe(7);
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
    expect(weekday("2026-10-29T09:00:00Z", "UTC", true)).toBe("Thursday");
  });

  it("day is the CLI's short date, in the given zone", () => {
    expect(day("2026-10-29T09:00:00Z", "UTC")).toBe("Thu 29 Oct");
    expect(day("2026-10-29T23:30:00Z", "Asia/Tokyo")).toBe("Fri 30 Oct");
  });

  it("dayMonth: a calendar date as it is, an instant in the given zone", () => {
    expect(dayMonth("2026-09-23")).toBe("23 Sep");
    expect(dayMonth("2026-01-01")).toBe("1 Jan");
    expect(dayMonth("2026-09-30T23:30:00Z", "UTC")).toBe("30 Sep");
    expect(dayMonth("2026-09-30T23:30:00Z", "Asia/Tokyo")).toBe("1 Oct");
  });

  it("clockTime", () => {
    expect(clockTime(new Date("2026-10-23T09:41:00Z"), "UTC")).toBe("9:41");
    expect(clockTime(new Date("2026-10-23T14:05:00Z"), "UTC")).toBe("14:05");
  });

  it("formatAgo words a gap as the CLI does", () => {
    const now = new Date("2026-10-23T12:00:00Z");
    const ago = (iso: string) => formatAgo(iso, now);
    expect(ago("2026-10-23T11:59:30Z")).toBe("a moment ago");
    expect(ago("2026-10-23T11:59:00Z")).toBe("1 minute ago");
    expect(ago("2026-10-23T11:15:00Z")).toBe("45 minutes ago");
    expect(ago("2026-10-23T11:00:00Z")).toBe("1 hour ago");
    expect(ago("2026-10-23T09:00:00Z")).toBe("3 hours ago");
    expect(ago("2026-10-23T12:00:30Z")).toBe("a moment ago");
  });
});
