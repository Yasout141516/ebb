export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The text of a failed response: the body's `error` or `notice`, else a plain fallback. */
export async function errorOf(response: Response): Promise<ApiError> {
  let message = `Something went wrong (${response.status}).`;
  try {
    const body = await response.json();
    if (typeof body?.error === "string") message = body.error;
    else if (typeof body?.notice === "string") message = body.notice;
    else if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string") {
      message = body.detail[0].msg;
    }
  } catch {
    // not JSON: keep the fallback
  }
  return new ApiError(response.status, message);
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

export function del(path: string, signal?: AbortSignal): Promise<void> {
  return request<void>(path, { method: "DELETE", signal });
}
