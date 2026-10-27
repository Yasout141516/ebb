import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("shows a failed first load of the sources, offers no Add source, and retries", async () => {
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path === "/subjects") return [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 }];
      throw new TypeError("Failed to fetch");
    });
    render(<SubjectColumn subjectId="s1" updates={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your sources.");
    expect(screen.queryByRole("link", { name: "Add source" })).toBeNull();
    expect(screen.queryByText(/sources?$/)).toBeNull(); // the 375 px bar claims no count
    vi.mocked(getJson).mockImplementation(async (path: string) =>
      path === "/subjects" ? [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 }] : [source(1)],
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Week_1.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add source" })).toBeInTheDocument();
    expect(screen.getByText("1 source")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("tries the sources again after an update", async () => {
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path === "/subjects") return [];
      throw new TypeError("Failed to fetch");
    });
    const { rerender } = render(<SubjectColumn subjectId="s1" updates={0} />);
    await screen.findByText("Couldn't load your sources.");
    vi.mocked(getJson).mockImplementation(async (path: string) => (path === "/subjects" ? [] : [source(1)]));
    rerender(<SubjectColumn subjectId="s1" updates={1} />);
    expect(await screen.findByText("Week_1.pdf")).toBeInTheDocument();
  });

  it("shows a failed first load of the subjects, with Retry", async () => {
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path === "/subjects") throw new TypeError("Failed to fetch");
      return [source(1)];
    });
    render(<SubjectColumn subjectId="s1" updates={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your subjects.");
    vi.mocked(getJson).mockImplementation(async (path: string) =>
      path === "/subjects" ? [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 }] : [source(1)],
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("8 concepts · 3 due today")).toBeInTheDocument();
  });
});

describe("the 375 px drawer", () => {
  beforeEach(() => {
    vi.mocked(getJson).mockImplementation(async (path: string) =>
      path === "/subjects" ? [{ id: "s1", name: "Computer Networks", concept_count: 8, due_count: 3 }] : [source(1)],
    );
  });

  async function opened() {
    render(
      <div>
        <SubjectColumn subjectId="s1" updates={0} />
        <main>The thread</main>
      </div>,
    );
    await screen.findByText("Week_1.pdf");
    const menu = screen.getByRole("button", { name: "Subjects and sources" });
    await userEvent.click(menu);
    return menu;
  }

  it("opens as a modal: focus moves in, Tab stays in, the rest is inert", async () => {
    const menu = await opened();
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const drawer = screen.getByRole("dialog", { name: "Subjects and sources" });
    expect(drawer).toHaveAttribute("aria-modal", "true");
    expect(drawer).toHaveFocus();
    expect(screen.getByText("The thread")).toHaveAttribute("inert");
    const links = within(drawer).getAllByRole("link");
    links[links.length - 1].focus();
    await userEvent.tab();
    expect(links[0]).toHaveFocus();
  });

  it("Escape closes it from inside and gives focus back to the menu button", async () => {
    const menu = await opened();
    within(screen.getByRole("dialog")).getAllByRole("link")[1].focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
    expect(screen.getByText("The thread")).not.toHaveAttribute("inert");
  });

  it("closes when the current subject is tapped", async () => {
    await opened();
    // Tapping the page you're on navigates nowhere; keep jsdom from trying.
    const stay = (e: Event) => e.preventDefault();
    document.addEventListener("click", stay, true);
    await userEvent.click(screen.getByRole("link", { name: /Computer Networks/ }));
    document.removeEventListener("click", stay, true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
