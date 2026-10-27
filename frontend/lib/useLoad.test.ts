import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { useLoad } from "./useLoad";

describe("useLoad", () => {
  it("is null while loading, then what it loaded", async () => {
    const { result } = renderHook(() => useLoad(async () => ({ n: 1 }), "a"));
    expect(result.current.loaded).toBeNull();
    await waitFor(() => expect(result.current.loaded).toEqual({ n: 1 }));
  });

  it("passes on the loader's own 'missing', and maps 404 and 422 to it", async () => {
    const own = renderHook(() => useLoad(async () => "missing", "a"));
    await waitFor(() => expect(own.result.current.loaded).toBe("missing"));
    for (const status of [404, 422]) {
      const { result } = renderHook(() =>
        useLoad(async () => {
          throw new ApiError(status, "no");
        }, "a"),
      );
      await waitFor(() => expect(result.current.loaded).toBe("missing"));
    }
  });

  it("any other failure is 'failed', and retry loads again", async () => {
    const load = vi.fn().mockRejectedValueOnce(new ApiError(500, "boom")).mockResolvedValue({ n: 2 });
    const { result } = renderHook(() => useLoad(load, "a"));
    await waitFor(() => expect(result.current.loaded).toBe("failed"));
    act(() => result.current.retry());
    expect(result.current.loaded).toBeNull();
    await waitFor(() => expect(result.current.loaded).toEqual({ n: 2 }));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("aborts the request when it unmounts, and ignores the abort", async () => {
    let signal: AbortSignal | undefined;
    const { result, unmount } = renderHook(() =>
      useLoad(
        (s) =>
          new Promise<{ n: number }>((_, reject) => {
            signal = s;
            s.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
        "a",
      ),
    );
    await waitFor(() => expect(signal).toBeDefined());
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(result.current.loaded).toBeNull();
  });
});
