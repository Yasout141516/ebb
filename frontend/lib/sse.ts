import { API_URL, ApiError, errorOf } from "./api";

export const UNREADABLE = "The answer came back garbled. Try again.";
export const CUT_OFF = "The connection closed before the answer finished. Try again.";

export type SseEvent = { event: string; data: unknown };

/**
 * Parse the events completed by `chunk`, after the unfinished text `carry` from before.
 * The new carry is the unfinished tail. Comment lines (keep-alives) are ignored.
 */
export function parseSse(chunk: string, carry: string): { events: SseEvent[]; carry: string } {
  let text = carry + chunk;
  // A "\r" ending the text may be the first half of "\r\n": hold it for the next chunk.
  const held = text.endsWith("\r") ? "\r" : "";
  if (held) text = text.slice(0, -1);
  text = text.replace(/\r\n?/g, "\n");
  const blocks = text.split("\n\n");
  const rest = (blocks.pop() ?? "") + held;
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length === 0) continue;
    try {
      events.push({ event, data: JSON.parse(data.join("\n")) });
    } catch {
      throw new ApiError(200, UNREADABLE);
    }
  }
  return { events, carry: rest };
}

/** POST `body` as JSON and hand each server-sent event to `onEvent` as it arrives. */
export async function streamPost(
  path: string,
  body: unknown,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  await readEvents(response, onEvent);
}

/** Read a server-sent event response to its end; a non-2xx answer rejects with ApiError. */
export async function readEvents(response: Response, onEvent: (e: SseEvent) => void): Promise<void> {
  if (!response.ok) throw await errorOf(response);
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let carry = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      // The server ends every event with a blank line: anything left over was cut off.
      if (carry.trim() !== "") throw new ApiError(200, CUT_OFF);
      return;
    }
    const parsed = parseSse(decoder.decode(value, { stream: true }), carry);
    carry = parsed.carry;
    for (const event of parsed.events) onEvent(event);
  }
}
