import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JobsView } from "./JobsView";

const RUNNING = {
  job_id: "j1", status: "running", request: { strategy: "ml-ranker" },
  submitted_at: "2026-07-27T10:00:00+00:00", started_at: "2026-07-27T10:00:01+00:00",
  finished_at: null, run_id: null, benchmark_run_id: null, error: null,
};
const DONE = { ...RUNNING, status: "succeeded", finished_at: "2026-07-27T10:00:09+00:00",
  run_id: "20260727T100009Z-abc123", benchmark_run_id: "20260727T100010Z-def456" };
const FAILED = { ...RUNNING, status: "failed", finished_at: "2026-07-27T10:00:04+00:00",
  error: "holding asset Asset(product_id=2) has no price on/before window start 2025-01-01" };

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

function stub(sequence: unknown[][]) {
  let i = 0;
  const fetchMock = vi.fn(async () => {
    const body = sequence[Math.min(i, sequence.length - 1)];
    i += 1;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("JobsView", () => {
  it("links a succeeded job to its backtest detail", async () => {
    stub([[DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    const link = await screen.findByRole("link", { name: /20260727T100009Z-abc123/ });
    expect(link).toHaveAttribute("href", "/backtest/20260727T100009Z-abc123");
  });

  it("shows the error text for a failed job", async () => {
    stub([[FAILED]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    expect(await screen.findByText(/no price on\/before window start/)).toBeInTheDocument();
  });

  it("keeps polling while a job is running, then stops when all are idle", async () => {
    const fetchMock = stub([[RUNNING], [RUNNING], [DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(screen.getByText("succeeded")).toBeInTheDocument());
    const afterSettled = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(afterSettled);
  });

  it("does not poll when the first response is already idle", async () => {
    const fetchMock = stub([[DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    await screen.findByText("succeeded");
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(1);
  });
});
