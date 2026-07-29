import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import detailFixture from "../../tests/fixtures/backtest-detail.json";
import fillsFixture from "../../tests/fixtures/fills.json";
import { BacktestDetail } from "./BacktestDetail";

// jsdom has no ResizeObserver; Recharts' ResponsiveContainer needs one to mount.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

afterEach(() => vi.restoreAllMocks());

function mount(detail: unknown = detailFixture, fills: unknown = fillsFixture) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    const body = url.includes("/fills") ? fills : detail;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  render(
    <MemoryRouter initialEntries={["/backtest/run-1"]}>
      <Routes><Route path="/backtest/:runId" element={<BacktestDetail />} /></Routes>
    </MemoryRouter>,
  );
  return calls;
}

describe("BacktestDetail", () => {
  it("renders the metric tiles from the summary", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/total_return/)).toBeInTheDocument());
  });

  it("renders a fills table with seeded sales flagged", async () => {
    mount();
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const seeded = (fillsFixture as { fills: { seeded: boolean }[] }).fills
      .filter((f) => f.seeded).length;
    expect(screen.getAllByText("seeded").length).toBe(seeded);
  });

  it("pages the fills table", async () => {
    // fillsFixture (n_fills: 1) can't exercise real paging — one row can never
    // fill a 100-row page, so `Next` would correctly stay disabled. Build a
    // payload with a genuine second page instead of touching the fixture.
    const paged = {
      ...fillsFixture,
      n_fills: 250,
      fills: Array.from({ length: 100 }, (_, i) => ({
        ...(fillsFixture as { fills: Record<string, unknown>[] }).fills[0],
        day: `2025-01-${String((i % 28) + 1).padStart(2, "0")}`,
      })),
    };
    const calls = mount(detailFixture, paged);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() =>
      expect(calls.some((u) => u.includes("offset=100"))).toBe(true));
  });

  it("disables Next when the run has no further pages", async () => {
    mount();
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("says so when a run has no benchmark", async () => {
    mount({ ...detailFixture, benchmark: null });
    await waitFor(() =>
      expect(screen.getByText(/no benchmark/i)).toBeInTheDocument());
  });

  it("surfaces a load error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unknown run_id: run-1" }), { status: 404 })));
    render(
      <MemoryRouter initialEntries={["/backtest/run-1"]}>
        <Routes><Route path="/backtest/:runId" element={<BacktestDetail />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("unknown run_id"));
  });
});
