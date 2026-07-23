import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import wfFixture from "../../tests/fixtures/walkforward.json";
import { WalkForwardDetail } from "./WalkForwardDetail";

// jsdom has no ResizeObserver; Recharts' ResponsiveContainer needs one to mount.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

afterEach(() => vi.restoreAllMocks());

function mount() {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(wfFixture), { status: 200 })));
  return render(
    <MemoryRouter initialEntries={["/walkforward/20260701T000000Z-aaa111"]}>
      <Routes>
        <Route path="/walkforward/:runId" element={<WalkForwardDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WalkForwardDetail", () => {
  it("shows the strategy, a fold row, the CI band, and the caveat", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/sealed-accumulation/)).toBeInTheDocument());
    expect(screen.getByText(/top_n/)).toBeInTheDocument();
    expect(screen.getByText(/95% CI/)).toBeInTheDocument();
    expect(screen.getByText(/-9\.00%/)).toBeInTheDocument();  // rigor.lo formatted
    expect(screen.getByText(/mark smoothing/i)).toBeInTheDocument();  // honesty caveat
  });
});
