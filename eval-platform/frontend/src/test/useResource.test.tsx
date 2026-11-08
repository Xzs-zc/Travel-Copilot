import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useResource } from "../hooks/useResource";

function mockResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("useResource", () => {
  it("loads JSON data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse({ value: 3 })));
    const { result } = renderHook(() => useResource<{ value: number }>("/api/value"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ value: 3 });
    expect(result.current.error).toBe("");
  });

  it("returns API errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse({ detail: "boom" }, 500)));
    const { result } = renderHook(() => useResource("/api/fail"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("boom");
  });
});
