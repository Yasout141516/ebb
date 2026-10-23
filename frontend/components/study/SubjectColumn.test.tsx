import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJson } from "@/lib/api";
import type { Source } from "@/lib/types";
import SourceList from "./SourceList";
import SubjectColumn from "./SubjectColumn";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJson: vi.fn(),
}));

const source = (n: number): Source => ({
  id: `d${n}`,
  filename: `Week_${n}.pdf`,
  kind: "pdf",
  page_count: 38,
  size_bytes: 4404019,
  chunk_count: 112,
  detail: "38 pages · 4.2 MB",
});

describe("SourceList", () => {
  it("lists each file with its detail line, and Add source opens the add screen", () => {
    render(<SourceList subjectId="s1" sources={[source(1), { ...source(2), filename: "notes.md", page_count: null, detail: "0.1 MB" }]} />);
    expect(screen.getByText("Week_1.pdf")).toBeInTheDocument();
    expect(screen.getByText("0.1 MB")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
    expect(screen.getByText("MD")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add source" })).toHaveAttribute("href", "/subjects/s1/add");
    expect(screen.getByText("PDF, TXT, MD · max 20 MB. Don't upload sensitive documents.")).toBeInTheDocument();
  });

  it("at 5 files Add source is disabled and reads 5 of 5 files", () => {
    render(<SourceList subjectId="s1" sources={[1, 2, 3, 4, 5].map(source)} />);
    expect(screen.queryByRole("link", { name: "Add source" })).toBeNull();
    const full = screen.getByRole("link", { name: "5 of 5 files" });
    expect(full).toHaveAttribute("aria-disabled", "true");
    expect(full).not.toHaveAttribute("href");
  });

  it("with no sources shows the empty state, leading to the add screen", () => {
    render(<SourceList subjectId="s1" sources={[]} />);
    expect(screen.getByText("No sources yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Grades will be marked “General knowledge” until you add notes. PDF, TXT or MD, up to 20 MB.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload notes" })).toHaveAttribute("href", "/subjects/s1/add");
  });
});

describe("SubjectColumn", () => {
  beforeEach(() => {
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path === "/subjects") {
        return [
          { id: "s2", name: "Organic Chemistry", concept_count: 6, due_count: 0 },
          { id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 },
        ];
      }
      if (path === "/subjects/s1/sources") return [source(1)];
      throw new Error(`unexpected ${path}`);
    });
  });

  it("lists every subject with its counts, the current one marked, and links to switch", async () => {
    render(<SubjectColumn subjectId="s1" updates={0} />);
    const current = await screen.findByRole("link", { name: /Computer Networks/ });
    expect(current).toHaveAttribute("aria-current", "page");
    expect(within(current).getByText("8 concepts · 3 due today")).toBeInTheDocument();
    const other = screen.getByRole("link", { name: /Organic Chemistry/ });
    expect(other).toHaveAttribute("href", "/study/s2");
    expect(other).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "New subject" })).toHaveAttribute("href", "/new");
    expect(await screen.findByText("Week_1.pdf")).toBeInTheDocument();
  });

  it("reloads the subjects' due counts after each update", async () => {
    const { rerender } = render(<SubjectColumn subjectId="s1" updates={0} />);
    await screen.findByText("8 concepts · 3 due today");
    vi.mocked(getJson).mockImplementation(async (path: string) =>
      path === "/subjects" ? [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 2 }] : [source(1)],
    );
    rerender(<SubjectColumn subjectId="s1" updates={1} />);
    await waitFor(() => expect(screen.getByText("8 concepts · 2 due today")).toBeInTheDocument());
  });
});
