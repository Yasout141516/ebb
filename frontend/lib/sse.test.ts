import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { CUT_OFF, UNREADABLE, parseSse, streamPost } from "./sse";

describe("parseSse", () => {
  it("parses an event with JSON data", () => {
    const { events, carry } = parseSse('event: step\ndata: {"label":"Grading"}\n\n', "");
    expect(events).toEqual([{ event: "step", data: { label: "Grading" } }]);
    expect(carry).toBe("");
  });

  it("keeps the carry when an event is split across chunks", () => {
    const first = parseSse('event: step\ndata: {"lab', "");
    expect(first.events).toEqual([]);
    const second = parseSse('el":"x"}\n\nevent: turn\ndata: {"a"', first.carry);
    expect(second.events).toEqual([{ event: "step", data: { label: "x" } }]);
    const third = parseSse(":1}\n\n", second.carry);
    expect(third.events).toEqual([{ event: "turn", data: { a: 1 } }]);
    expect(third.carry).toBe("");
  });

  it("joins multi-line data with newlines", () => {
    const { events } = parseSse('event: x\ndata: {"a":\ndata: 1}\n\n', "");
    expect(events).toEqual([{ event: "x", data: { a: 1 } }]);
  });

  it("ignores comments and keep-alives", () => {
    const { events } = parseSse(": keep-alive\n\nevent: x\n: note\ndata: 2\n\n", "");
    expect(events).toEqual([{ event: "x", data: 2 }]);
  });

  it("handles CRLF line endings", () => {
    const { events } = parseSse('event: x\r\ndata: {"a":1}\r\n\r\n', "");
    expect(events).toEqual([{ event: "x", data: { a: 1 } }]);
  });

  it("handles a CRLF split across chunks", () => {
    const first = parseSse('event: x\r\ndata: {"a":1}\r\n\r', "");
    expect(first.events).toEqual([]);
    const second = parseSse("\nevent: y\r\ndata: 2\r\n\r\n", first.carry);
    expect(second.events).toEqual([
      { event: "x", data: { a: 1 } },
      { event: "y", data: 2 },
    ]);
  });

  it("rejects data that is not JSON", () => {
    expect(() => parseSse("event: x\ndata: {oops\n\n", "")).toThrow(UNREADABLE);
  });

  it("names an event without an event line 'message'", () => {
    const { events } = parseSse("data: 1\n\n", "");
    expect(events).toEqual([{ event: "message", data: 1 }]);
  });
});

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("streamPost", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("delivers events across chunk boundaries", async () => {
    const body = streamOf(['event: step\ndata: {"label":"a"}\n', '\nevent: turn\ndata: {"x":1}\n\n']);
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const seen: unknown[] = [];
    await streamPost("/sessions/t/reply", { driver: "d" }, (e) => seen.push(e));
    expect(seen).toEqual([
      { event: "step", data: { label: "a" } },
      { event: "turn", data: { x: 1 } },
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/sessions/t/reply");
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"driver":"d"}');
  });

  it("rejects with the error text of a 409", async () => {
    const response = new Response(JSON.stringify({ error: "Busy." }), { status: 409 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(streamPost("/x", {}, () => {})).rejects.toMatchObject({
      status: 409,
      message: "Busy.",
    });
  });

  it("rejects with the notice text of a 503", async () => {
    const response = new Response(JSON.stringify({ notice: "Tutor is down." }), { status: 503 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const error = await streamPost("/x", {}, () => {}).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, message: "Tutor is down." });
  });

  it("rejects when the stream ends mid-event", async () => {
    const body = streamOf(['event: step\ndata: {"label":"a"}\n\nevent: turn\ndata: {"x"']);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const seen: unknown[] = [];
    const error = await streamPost("/x", {}, (e) => seen.push(e)).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe(CUT_OFF);
    expect(seen).toEqual([{ event: "step", data: { label: "a" } }]);
  });

  it("rejects when a data line is not JSON", async () => {
    const body = streamOf(["event: step\ndata: nope\n\n"]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    await expect(streamPost("/x", {}, () => {})).rejects.toMatchObject({ message: UNREADABLE });
  });
});
