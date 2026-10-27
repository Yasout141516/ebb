import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, messageOf, uploadFile } from "./api";
import type { SseEvent } from "./sse";
import { CUT_OFF } from "./sse";

/** A stand-in XMLHttpRequest the test drives by hand. */
class FakeXhr {
  static last: FakeXhr;
  method = "";
  url = "";
  body: FormData | null = null;
  status = 0;
  responseText = "";
  upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
  onprogress: (() => void) | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader() {}
  send(body: FormData) {
    this.body = body;
  }
  aborted = false;
  abort() {
    this.aborted = true;
  }

  sent(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
  }
  receive(status: number, text: string) {
    this.status = status;
    this.responseText += text;
    this.onprogress?.();
  }
  finish() {
    this.onload?.();
  }
}

describe("uploadFile", () => {
  afterEach(() => vi.unstubAllGlobals());

  function start(onProgress = vi.fn(), events: SseEvent[] = []) {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const file = new File(["hello"], "Week 1 & 2.md");
    const done = uploadFile("/drafts/d1/files", file, onProgress, (e) => events.push(e));
    return { done, xhr: FakeXhr.last, events, onProgress };
  }

  it("posts the file with its name in the query and reports progress", async () => {
    const { done, xhr, onProgress } = start();
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe(`${API_URL}/drafts/d1/files?name=Week%201%20%26%202.md`);
    expect((xhr.body?.get("file") as File).name).toBe("Week 1 & 2.md");
    xhr.sent(50, 200);
    expect(onProgress).toHaveBeenCalledWith(25);
    xhr.receive(200, "");
    xhr.finish();
    await done;
  });

  it("hands over each event as its text arrives", async () => {
    const { done, xhr, events } = start();
    xhr.receive(200, 'event: file\ndata: {"status":"reading"}\n\nevent: file\ndata: {"sta');
    expect(events).toEqual([{ event: "file", data: { status: "reading" } }]);
    xhr.receive(200, 'tus":"finding"}\n\n');
    expect(events).toHaveLength(2);
    expect(events[1].data).toEqual({ status: "finding" });
    xhr.finish();
    await done;
    expect(events).toHaveLength(2);
  });

  it("rejects a stream cut off mid-event", async () => {
    const { done, xhr } = start();
    xhr.receive(200, 'event: file\ndata: {"status"');
    xhr.finish();
    await expect(done).rejects.toEqual(new ApiError(200, CUT_OFF));
  });

  it("rejects a non-2xx answer with the body's error", async () => {
    const { done, xhr, events } = start();
    xhr.receive(410, '{"error":"This upload expired — add the files again."}');
    xhr.finish();
    await expect(done).rejects.toMatchObject({
      status: 410,
      message: "This upload expired — add the files again.",
    });
    expect(events).toEqual([]);
  });

  it("aborts the request when its signal is aborted", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const controller = new AbortController();
    const done = uploadFile("/drafts/d1/files", new File(["x"], "a.md"), vi.fn(), vi.fn(), controller.signal);
    const xhr = FakeXhr.last;
    controller.abort();
    expect(xhr.aborted).toBe(true);
    await expect(done).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects when the connection fails", async () => {
    const { done, xhr } = start();
    xhr.onerror?.();
    await expect(done).rejects.toBeInstanceOf(ApiError);
  });
});

describe("messageOf", () => {
  it("uses the API message, names a network failure, and falls back to a plain line", () => {
    expect(messageOf(new ApiError(409, "That name is taken."))).toBe("That name is taken.");
    expect(messageOf(new TypeError("Failed to fetch"))).toBe("Can't reach the API.");
    expect(messageOf(new Error("boom"))).toBe("Something went wrong — try again.");
  });
});
