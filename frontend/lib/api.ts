// sse.ts imports from this module too; each side uses the other only inside functions.
import { CUT_OFF, parseSse, type SseEvent } from "./sse";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The error a failed answer carries: the body's `error` or `notice`, else a plain fallback. */
function errorFrom(status: number, text: string): ApiError {
  let message = `Something went wrong (${status}).`;
  try {
    const body = JSON.parse(text);
    if (typeof body?.error === "string") message = body.error;
    else if (typeof body?.notice === "string") message = body.notice;
    else if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string") {
      message = body.detail[0].msg;
    }
  } catch {
    // not JSON: keep the fallback
  }
  return new ApiError(status, message);
}

/** The text of a failed response: the body's `error` or `notice`, else a plain fallback. */
export async function errorOf(response: Response): Promise<ApiError> {
  let text = "";
  try {
    text = await response.text();
  } catch {
    // unreadable body: keep the fallback
  }
  return errorFrom(response.status, text);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) throw await errorOf(response);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal });
}

export function postJson<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
}

/**
 * POST with no body as the page goes away: `keepalive` lets the request outlive the page,
 * and without a body (or its JSON content type) the browser needs no preflight first.
 * Nothing waits for the answer.
 */
export function postOnLeave(path: string): void {
  fetch(`${API_URL}${path}`, { method: "POST", keepalive: true }).catch(() => {
    // the page is gone: there is no one to tell
  });
}

export function del(path: string, signal?: AbortSignal): Promise<void> {
  return request<void>(path, { method: "DELETE", signal });
}

/**
 * Upload `file` (multipart field `file`, its name also in `?name=`) and hand each
 * server-sent event of the answer to `onEvent` as it arrives. XMLHttpRequest, not fetch,
 * because only it reports how much of the body has been sent: `onProgress` gets 0–100.
 * Aborting `signal` aborts the request and rejects with an AbortError.
 */
export function uploadFile(
  path: string,
  file: File,
  onProgress: (percent: number) => void,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Upload cancelled", "AbortError"));
    const xhr = new XMLHttpRequest();
    const separator = path.includes("?") ? "&" : "?";
    xhr.open("POST", `${API_URL}${path}${separator}name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Accept", "text/event-stream");
    let consumed = 0;
    let carry = "";
    let failed = false;
    const ok = () => xhr.status >= 200 && xhr.status < 300;
    const onAbort = () => fail(new DOMException("Upload cancelled", "AbortError"));
    const fail = (error: unknown) => {
      failed = true;
      signal?.removeEventListener("abort", onAbort);
      xhr.abort();
      reject(error);
    };
    // responseText grows as the answer arrives: parse only what is new since last time.
    const consume = () => {
      if (failed || !ok()) return;
      const text = xhr.responseText;
      const chunk = text.slice(consumed);
      consumed = text.length;
      try {
        const parsed = parseSse(chunk, carry);
        carry = parsed.carry;
        for (const event of parsed.events) onEvent(event);
      } catch (error) {
        fail(error);
      }
    };
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(Math.round((e.loaded * 100) / e.total));
    };
    xhr.onprogress = consume;
    xhr.onload = () => {
      if (failed) return;
      if (!ok()) return fail(errorFrom(xhr.status, xhr.responseText));
      consume();
      if (failed) return;
      // The server ends every event with a blank line: anything left over was cut off.
      if (carry.trim() !== "") return fail(new ApiError(200, CUT_OFF));
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    xhr.onerror = () => {
      if (!failed) fail(new ApiError(0, CUT_OFF));
    };
    signal?.addEventListener("abort", onAbort);
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
