import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "./client";

afterEach(() => vi.restoreAllMocks());

describe("apiClient", () => {
  it("lists runs from /api/runs", async () => {
    const fake = [{ run_id: "x", command: "walkforward", strategy: "s", recorded_at: "2026",
      git_sha: "abc", git_dirty: false, results: { stitched_total_return: -0.02 } }];
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify(fake), { status: 200 })));
    const runs = await apiClient.listRuns();
    expect(runs[0].run_id).toBe("x");
  });

  it("passes command filter as a query param", async () => {
    const spy = vi.fn(async () => new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", spy);
    await apiClient.listRuns({ command: "evaluate" });
    expect(String((spy.mock.calls[0] as unknown[])[0])).toContain("command=evaluate");
  });

  it("throws a helpful error on 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unknown run_id: z" }), { status: 404 })));
    await expect(apiClient.getRun("z")).rejects.toThrow("unknown run_id: z");
  });
});
