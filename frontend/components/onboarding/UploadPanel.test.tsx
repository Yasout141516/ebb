import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, del, postJson, uploadFile } from "@/lib/api";
import type { SseEvent } from "@/lib/sse";
import type { DraftFileEvent, Source, SourceFileEvent } from "@/lib/types";
import UploadPanel from "./UploadPanel";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  postJson: vi.fn(),
  del: vi.fn(),
  uploadFile: vi.fn(),
}));

const EXISTS = "Computer Networks already exists — add notes to it from its Sources list.";
const EXPIRED = "This upload expired — add the files again.";

type Upload = {
  path: string;
  file: File;
  signal?: AbortSignal;
  progress: (percent: number) => void;
  emit: (data: DraftFileEvent | SourceFileEvent) => void;
  end: () => void;
};
let uploads: Upload[];

beforeEach(() => {
  vi.clearAllMocks();
  uploads = [];
  vi.mocked(del).mockResolvedValue(undefined);
  vi.mocked(uploadFile).mockImplementation(
    (path, file, onProgress, onEvent: (e: SseEvent) => void, signal?: AbortSignal) =>
      new Promise<void>((resolve, reject) => {
        signal?.addEventListener("abort", () =>
          reject(new DOMException("Upload cancelled", "AbortError")),
        );
        uploads.push({
          path,
          file,
          signal,
          progress: (p) => act(() => onProgress(p)),
          emit: (data) => act(() => onEvent({ event: "file", data })),
          end: () => act(() => resolve()),
        });
      }),
  );
  vi.mocked(postJson).mockImplementation(async (path: string) => {
    if (path === "/drafts") return { id: "d1" };
    if (path === "/drafts/d1/commit") return { subject_id: "s1" };
    throw new Error(`unexpected ${path}`);
  });
});

function pdf(name: string, size = 1000): File {
  const file = new File(["%PDF"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

const input = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const chooseButton = () => screen.getByRole("button", { name: "Choose file" });
const startButton = () => screen.getByRole("button", { name: /Start studying/ });

function ready(n: number, concepts = ["TCP handshake"], fresh = concepts): DraftFileEvent {
  return {
    status: "ready",
    file_id: `f${n}`,
    filename: `w${n}.pdf`,
    page_count: 38,
    passages: 112,
    concepts,
    new: fresh,
  };
}

async function named(name = "Networks") {
  const user = userEvent.setup({ applyAccept: false });
  render(<UploadPanel mode="draft" subjects={[]} />);
  await user.type(screen.getByLabelText("Subject name"), name);
  return user;
}

/** Choose `files` in the drop zone's file input. */
async function addFiles(user: ReturnType<typeof userEvent.setup>, files: File[]) {
  await user.upload(input(), files);
}

async function nextUpload(index: number): Promise<Upload> {
  await waitFor(() => expect(uploads.length).toBeGreaterThan(index));
  return uploads[index];
}

describe("UploadPanel, a new subject", () => {
  it("keeps the drop zone disabled until a name is entered", async () => {
    const user = userEvent.setup();
    render(<UploadPanel mode="draft" subjects={[]} />);
    expect(chooseButton()).toBeDisabled();
    expect(input()).toBeDisabled();
    await user.type(screen.getByLabelText("Subject name"), "  ");
    expect(chooseButton()).toBeDisabled();
    await user.type(screen.getByLabelText("Subject name"), "Networks");
    expect(chooseButton()).toBeEnabled();
  });

  it("shows the exists copy for a name another subject has", async () => {
    const user = userEvent.setup();
    const subjects = [{ id: "s0", name: "Computer Networks", concept_count: 8, due_count: 0 }];
    render(<UploadPanel mode="draft" subjects={subjects} />);
    await user.type(screen.getByLabelText("Subject name"), "computer networks");
    expect(screen.getByText(EXISTS)).toBeInTheDocument();
    expect(chooseButton()).toBeDisabled();
  });

  it("compares names as the API does: Straße and STRASSE are two subjects", async () => {
    const user = userEvent.setup();
    const subjects = [{ id: "s0", name: "Straße", concept_count: 8, due_count: 0 }];
    render(<UploadPanel mode="draft" subjects={subjects} />);
    await user.type(screen.getByLabelText("Subject name"), "STRASSE");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(chooseButton()).toBeEnabled();
  });

  it("shows the API's exists copy on a 409 and uploads nothing", async () => {
    vi.mocked(postJson).mockRejectedValue(new ApiError(409, EXISTS));
    const user = await named("Computer Networks");
    await addFiles(user, [pdf("w1.pdf")]);
    expect(await screen.findByText(EXISTS)).toBeInTheDocument();
    expect(postJson).toHaveBeenCalledWith("/drafts", { name: "Computer Networks" });
    expect(uploadFile).not.toHaveBeenCalled();
    expect(chooseButton()).toBeDisabled();
  });

  it("walks a row through uploading, reading and finding to ready", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf", 4.2 * 1024 * 1024)]);
    const upload = await nextUpload(0);
    expect(upload.path).toBe("/drafts/d1/files");
    upload.progress(40);
    expect(screen.getByText("4.2 MB · Uploading 40%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByLabelText("Subject name")).toHaveAttribute("readonly");
    upload.emit({ status: "reading" });
    expect(screen.getByText("4.2 MB · Reading…")).toBeInTheDocument();
    upload.emit({ status: "finding" });
    expect(screen.getByText("4.2 MB · Finding concepts…")).toBeInTheDocument();
    upload.emit(ready(1, ["TCP handshake", "DNS resolution"]));
    upload.end();
    expect(screen.getByText("4.2 MB · 38 pages · 112 passages")).toBeInTheDocument();
    expect(screen.getByText("2 concepts found:")).toBeInTheDocument();
    expect(screen.getByText("DNS resolution")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("marks which of a row's concepts are new", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf")]);
    const upload = await nextUpload(0);
    upload.emit(ready(1, ["Subnetting", "NAT", "UDP"], ["UDP"]));
    upload.end();
    expect(screen.getByText("3 concepts found:")).toBeInTheDocument();
    expect(screen.getAllByText("(new)")).toHaveLength(1);
    expect(screen.getByText("(new)").parentElement).toHaveTextContent("UDP");
  });

  it("announces each step of a row politely", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf")]);
    const upload = await nextUpload(0);
    upload.emit({ status: "finding" });
    expect(screen.getByText("w1.pdf: finding concepts")).toHaveAttribute("aria-live", "polite");
    upload.emit(ready(1));
    upload.end();
    expect(screen.getByText("w1.pdf: ready, 1 concepts found")).toHaveAttribute("aria-live", "polite");
  });

  it("ignores upload progress once the file is being read", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf", 4.2 * 1024 * 1024)]);
    const upload = await nextUpload(0);
    upload.emit({ status: "reading" });
    upload.progress(100);
    expect(screen.getByText("4.2 MB · Reading…")).toBeInTheDocument();
  });

  it("refuses a wrong type or an oversize file on its row without uploading", async () => {
    const user = await named();
    await addFiles(user, [pdf("slides.pptx"), pdf("Textbook_full_scan.pdf", 27_682_406)]);
    expect(await screen.findByText("Ebb reads PDF, TXT or MD files.")).toBeInTheDocument();
    expect(
      screen.getByText("Textbook_full_scan.pdf is 26.4 MB — over the 20 MB limit."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Split it into chapters, or upload just the pages you're studying."),
    ).toBeInTheDocument();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(startButton()).toBeDisabled();
  });

  it("shows a refused row's message and doesn't count it", async () => {
    const user = await named();
    await addFiles(user, [1, 2, 3, 4, 5].map((n) => pdf(`w${n}.pdf`)));
    for (let i = 0; i < 4; i++) {
      const upload = await nextUpload(i);
      upload.emit(ready(i + 1));
      upload.end();
    }
    const last = await nextUpload(4);
    last.emit({ status: "reading" });
    last.emit({ status: "refused", message: "w5.pdf is already in Networks.", retry: false });
    last.end();
    expect(screen.getByText("w5.pdf is already in Networks.")).toBeInTheDocument();
    expect(screen.queryByText("5 of 5 files")).not.toBeInTheDocument();
    expect(chooseButton()).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(startButton()).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("w5.pdf is already in Networks.");
  });

  it("removes a refused row with ✕, without asking the API", async () => {
    const user = await named();
    await addFiles(user, [pdf("slides.pptx")]);
    await user.click(await screen.findByRole("button", { name: "Remove slides.pptx" }));
    expect(screen.queryByText("Ebb reads PDF, TXT or MD files.")).not.toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();
  });

  it("Start over cancels the draft's uploads and takes back its ready files", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf"), pdf("w2.pdf"), pdf("w3.pdf")]);
    const first = await nextUpload(0);
    first.emit(ready(1));
    first.end();
    const second = await nextUpload(1); // in flight; w3 waits for its first event
    expect(uploads).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Start over" }));
    expect(second.signal?.aborted).toBe(true);
    expect(del).toHaveBeenCalledWith("/drafts/d1/files/f1");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(uploads).toHaveLength(2); // w3 never went to the old draft
    expect(screen.queryByText("w2.pdf")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Subject name")).not.toHaveAttribute("readonly");
  });

  it("uploads files one at a time, in the order added", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf"), pdf("w2.pdf")]);
    const first = await nextUpload(0);
    expect(uploads).toHaveLength(1);
    first.emit({ status: "reading" });
    const second = await nextUpload(1);
    expect(second.file.name).toBe("w2.pdf");
  });

  it("disables Start studying while a row is finding concepts", async () => {
    const user = await named();
    await addFiles(user, [pdf("w1.pdf"), pdf("w2.pdf")]);
    const first = await nextUpload(0);
    first.emit(ready(1));
    first.end();
    const second = await nextUpload(1);
    second.emit({ status: "finding" });
    expect(startButton()).toBeDisabled();
    second.emit(ready(2));
    second.end();
    expect(startButton()).toBeEnabled();
    await user.click(startButton());
    expect(postJson).toHaveBeenCalledWith("/drafts/d1/commit");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/study/s1"));
  });

  it("disables the drop zone with 5 of 5 files once five are ready", async () => {
    const user = await named();
    await addFiles(user, [1, 2, 3, 4, 5].map((n) => pdf(`w${n}.pdf`)));
    for (let i = 0; i < 5; i++) {
      const upload = await nextUpload(i);
      upload.emit(ready(i + 1));
      upload.end();
    }
    expect(screen.getByText("5 of 5 files")).toBeInTheDocument();
    expect(chooseButton()).toBeDisabled();
  });

  it("refuses a sixth file with the API's copy", async () => {
    const user = await named();
    await addFiles(user, [1, 2, 3, 4, 5, 6].map((n) => pdf(`w${n}.pdf`)));
    expect(await screen.findByText("Networks can have at most 5 files.")).toBeInTheDocument();
  });

  it("removes a ready file with ✕", async () => {
    vi.mocked(del).mockResolvedValue(undefined);
    const user = await named();
    await addFiles(user, [pdf("w1.pdf")]);
    const upload = await nextUpload(0);
    upload.emit(ready(1));
    upload.end();
    await user.click(screen.getByRole("button", { name: "Remove w1.pdf" }));
    expect(del).toHaveBeenCalledWith("/drafts/d1/files/f1");
    await waitFor(() => expect(screen.queryByText("w1.pdf")).not.toBeInTheDocument());
    expect(startButton()).toBeDisabled();
  });

  it("offers Try again when the tutor was busy, and sends the same file", async () => {
    const user = await named();
    const file = pdf("w1.pdf");
    await addFiles(user, [file]);
    const upload = await nextUpload(0);
    upload.emit({ status: "refused", message: "The tutor is busy.", retry: true });
    upload.end();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    const again = await nextUpload(1);
    expect(again.file).toBe(file);
    expect(screen.queryByText("The tutor is busy.")).not.toBeInTheDocument();
  });

  it("shows the expired copy with Start over when the draft is gone", async () => {
    vi.mocked(postJson).mockImplementation(async (path: string) => {
      if (path === "/drafts") return { id: "d1" };
      throw new ApiError(410, EXPIRED);
    });
    const user = await named();
    await addFiles(user, [pdf("w1.pdf")]);
    const upload = await nextUpload(0);
    upload.emit(ready(1));
    upload.end();
    await user.click(startButton());
    expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Start over" })[0]);
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
    expect(screen.queryByText("w1.pdf")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Subject name")).not.toHaveAttribute("readonly");
    expect(screen.getByLabelText("Subject name")).toHaveValue("Networks");
  });
});

describe("UploadPanel, adding to a subject", () => {
  const subject = { id: "s1", name: "Computer Networks", concept_count: 8, due_count: 0 };
  const source = (n: number): Source => ({
    id: `doc${n}`,
    filename: `w${n}.pdf`,
    kind: "pdf",
    page_count: 10,
    size_bytes: 1000,
    chunk_count: 20,
    detail: "10 pages · 0.0 MB",
  });

  it("shows the saved report", async () => {
    const user = userEvent.setup();
    render(<UploadPanel mode="add" subject={subject} sources={[source(1)]} />);
    expect(screen.getByText("w1.pdf")).toBeInTheDocument();
    await user.upload(input(), pdf("w2.pdf"));
    const upload = await nextUpload(0);
    expect(upload.path).toBe("/subjects/s1/sources");
    upload.emit({ status: "finding" });
    expect(chooseButton()).toBeDisabled();
    upload.emit({
      status: "saved",
      detail: "38 pages · 4.2 MB",
      matched: ["Subnetting", "DNS"],
      fresh: [
        { name: "NAT", description: "" },
        { name: "UDP", description: "" },
      ],
    });
    upload.end();
    expect(
      screen.getByText("4 concepts in these notes — 2 already here (Subnetting, DNS), 2 new:"),
    ).toBeInTheDocument();
    expect(screen.getByText("NAT")).toBeInTheDocument();
    expect(chooseButton()).toBeEnabled();
  });

  it("is full at 5 of 5 files", () => {
    render(<UploadPanel mode="add" subject={subject} sources={[1, 2, 3, 4, 5].map(source)} />);
    expect(screen.getByText("5 of 5 files")).toBeInTheDocument();
    expect(chooseButton()).toBeDisabled();
  });
});
