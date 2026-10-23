import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Offer } from "@/lib/types";
import ResumeCard from "./ResumeCard";

const NOW = new Date("2026-10-23T12:00:00Z");
const offer = (over: Partial<Offer> = {}): Offer => ({
  thread_id: "t1",
  concept: "DNS resolution",
  number: 2,
  last_active_at: "2026-10-23T09:00:00Z",
  busy: false,
  ...over,
});

describe("ResumeCard", () => {
  it("says where the session stopped and offers Resume and Start fresh", async () => {
    const onResume = vi.fn();
    const onFresh = vi.fn();
    render(<ResumeCard offer={offer()} now={NOW} onResume={onResume} onStartFresh={onFresh} />);
    expect(screen.getByText("You stopped on DNS resolution, question 2, 3 hours ago.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onResume).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Start fresh" }));
    expect(onFresh).toHaveBeenCalled();
  });

  it("offers only Start fresh when the session is open elsewhere", () => {
    render(<ResumeCard offer={offer({ busy: true })} now={NOW} onResume={() => {}} onStartFresh={() => {}} />);
    expect(screen.getByText("That session is open in another tab or terminal.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start fresh" })).toBeInTheDocument();
  });
});
