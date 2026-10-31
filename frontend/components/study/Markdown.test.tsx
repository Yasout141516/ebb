import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

describe("Markdown", () => {
  it("renders a table", () => {
    const text = "## Seeing it work\n\n| Step | Flags |\n|---|---|\n| 1 | SYN |\n| 2 | SYN, ACK |";
    render(<Markdown text={text} />);
    expect(screen.getByRole("heading", { name: "Seeing it work", level: 2 })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(screen.getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Step", "Flags"]);
    expect(table).toHaveTextContent("SYN, ACK");
  });

  it("shows <script> and raw HTML as text and drops javascript: links", () => {
    const text = [
      "Tags look like `<script>alert(1)</script>` in code.",
      "",
      '<script>alert("x")</script>',
      "",
      'Some <b onclick="alert(2)">bold</b> words and <img src=x onerror="alert(3)">.',
      "",
      "[click me](javascript:alert(4)) and [a page](https://example.com/a) and [home](/study)",
    ].join("\n");
    const { container } = render(<Markdown text={text} />);
    // In code it is text; raw HTML is never an element.
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script, b, img, [onclick], [onerror]")).toBeNull();
    expect(container).toHaveTextContent("Some bold words");
    // Only the http(s) link is a link; the others keep their words.
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "a page" })).toHaveAttribute("href", "https://example.com/a");
    expect(screen.getByRole("link", { name: "a page" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("click me")).not.toHaveAttribute("href");
    expect(screen.getByText("home")).not.toHaveAttribute("href");
  });
});
