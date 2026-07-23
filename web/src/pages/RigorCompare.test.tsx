import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import evalFixture from "../../tests/fixtures/evaluate.json";
import { RigorCompare } from "./RigorCompare";

afterEach(() => vi.restoreAllMocks());

function mount() {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(evalFixture), { status: 200 })));
  return render(
    <MemoryRouter initialEntries={["/evaluate/20260702T000000Z-bbb222"]}>
      <Routes><Route path="/evaluate/:runId" element={<RigorCompare />} /></Routes>
    </MemoryRouter>,
  );
}

describe("RigorCompare", () => {
  it("shows the Reality Check headline and a row per strategy", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/Reality Check/)).toBeInTheDocument());
    expect(screen.getByText(/p = 1\.0000/)).toBeInTheDocument();
    expect(screen.getByText("ml-ranker")).toBeInTheDocument();
    expect(screen.getByText("sealed-accumulation")).toBeInTheDocument();
    expect(screen.getByText(/mark smoothing/i)).toBeInTheDocument();
  });

  it("sorts strategies by deflated Sharpe when that header is clicked", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("ml-ranker")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /deflated sharpe/i }));
    const rows = screen.getAllByTestId("strategy-row");
    // sealed-accumulation dsr 0.008 > ml-ranker 0.007: descending puts sealed first
    expect(rows[0]).toHaveTextContent("sealed-accumulation");
  });
});
