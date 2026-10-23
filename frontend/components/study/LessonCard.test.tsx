import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LessonCard from "./LessonCard";

describe("LessonCard", () => {
  it("shows the lesson under its title", () => {
    render(<LessonCard lesson={{ text: "A /26 leaves 6 host bits.", citations: [] }} />);
    expect(screen.getByRole("heading", { name: "Let's fix that" })).toBeInTheDocument();
    expect(screen.getByText("A /26 leaves 6 host bits.")).toBeInTheDocument();
  });

  it("leaves out the sources row when no citation is usable", () => {
    const blank = { kind: "chunk" as const, ref: "c", label: "", page: null, quote: "", name: "", heading: null, title: null };
    const { container } = render(<LessonCard lesson={{ text: "Lesson.", citations: [blank] }} />);
    expect(container.querySelector("section")!.children).toHaveLength(3);
  });
});
