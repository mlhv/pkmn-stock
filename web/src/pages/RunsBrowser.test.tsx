import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import runsFixture from "../../tests/fixtures/runs.json";
import { RunsBrowser } from "./RunsBrowser";

afterEach(() => vi.restoreAllMocks());

function mountWith(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(data), { status: 200 })));
  return render(<MemoryRouter><RunsBrowser /></MemoryRouter>);
}

describe("RunsBrowser", () => {
  it("renders a row per run with a link to its detail", async () => {
    mountWith(runsFixture);
    await waitFor(() => expect(screen.getByText("sealed-accumulation")).toBeInTheDocument());
    const wfLink = screen.getByRole("link", { name: /20260701T000000Z-aaa111/ });
    expect(wfLink).toHaveAttribute("href", "/walkforward/20260701T000000Z-aaa111");
    const evalLink = screen.getByRole("link", { name: /20260702T000000Z-bbb222/ });
    expect(evalLink).toHaveAttribute("href", "/evaluate/20260702T000000Z-bbb222");
  });

  it("shows an error state when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    render(<MemoryRouter><RunsBrowser /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });

  it("links a backtest run to its detail screen", async () => {
    mountWith([{
      run_id: "20260727T100009Z-abc123", recorded_at: "2026-07-27T10:00:09+00:00",
      command: "backtest", strategy: "sealed-accumulation", git_sha: "abc1234",
      git_dirty: false, results: { total_return: -0.02 },
    }]);
    const link = await screen.findByRole("link", { name: /20260727T100009Z-abc123/ });
    expect(link).toHaveAttribute("href", "/backtest/20260727T100009Z-abc123");
  });
});
