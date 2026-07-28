import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import strategiesFixture from "../../tests/fixtures/strategies.json";
import productsFixture from "../../tests/fixtures/products.json";
import { NewRun } from "./NewRun";

afterEach(() => vi.restoreAllMocks());

function stubRoutes(overrides: Record<string, unknown> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.startsWith("/api/strategies"))
      return new Response(JSON.stringify(strategiesFixture), { status: 200 });
    if (url.startsWith("/api/products"))
      return new Response(JSON.stringify(productsFixture), { status: 200 });
    if (url.startsWith("/api/backtest"))
      return new Response(JSON.stringify(overrides.job ?? {
        job_id: "j1", status: "queued", request: {}, submitted_at: "2026-07-27T00:00:00+00:00",
        started_at: null, finished_at: null, run_id: null, benchmark_run_id: null, error: null,
      }), { status: 202 });
    return new Response("{}", { status: 404 });
  }));
  return calls;
}

describe("NewRun", () => {
  it("generates param inputs from the selected strategy's ParamSpecs", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "sealed-accumulation" },
    });
    const takeProfit = await screen.findByLabelText("take_profit");
    expect(takeProfit).toHaveAttribute("min", "1.2");
    expect(takeProfit).toHaveAttribute("max", "2.5");
    expect(screen.getByLabelText("min_age_days")).toBeInTheDocument();
  });

  it("swaps the inputs when the strategy changes", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "sealed-accumulation" },
    });
    await screen.findByLabelText("take_profit");
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "xs-momentum" } });
    await screen.findByLabelText("lookback_days");
    expect(screen.queryByLabelText("take_profit")).not.toBeInTheDocument();
  });

  it("shows no param inputs for buy-and-hold", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    expect(screen.getByText(/no tunable parameters/i)).toBeInTheDocument();
  });

  it("posts the assembled request", async () => {
    const calls = stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2025-01-01" } });
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "2025-01-08" } });
    fireEvent.change(screen.getByLabelText("Starting cash"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: /run backtest/i }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/backtest")).toBe(true));
    const posted = JSON.parse(
      String(calls.find((c) => c.url === "/api/backtest")?.init?.body),
    );
    expect(posted).toMatchObject({
      strategy: "buy-and-hold", start: "2025-01-01", end: "2025-01-08", cash: 500,
      impact: true, holdings: [],
    });
  });

  it("surfaces a rejected submission without navigating", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/strategies"))
        return new Response(JSON.stringify(strategiesFixture), { status: 200 });
      return new Response(JSON.stringify({ detail: "start is after end" }), { status: 400 });
    }));
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /run backtest/i }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("start is after end"));
  });

  it("adds a holding row from a product search hit", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Product search"), { target: { value: "box" } });
    const hit = await screen.findByRole("button", { name: /Alpha Booster Box/ });
    fireEvent.click(hit);
    expect(await screen.findByDisplayValue("Alpha Booster Box")).toBeInTheDocument();
    expect(screen.getByLabelText("Sub type")).toHaveValue("Normal");
  });
});
