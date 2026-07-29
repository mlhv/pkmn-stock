import type {
  BacktestDetail, BacktestRequest, EvaluateResponse, FillsPage, JobStatus, ProductHit,
  RunDetail, RunSummary, StrategyInfo, WalkForwardResponse,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (parsed.detail) detail = JSON.stringify(parsed.detail);
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const apiClient = {
  listRuns: (filter?: { command?: string; strategy?: string }): Promise<RunSummary[]> => {
    const q = new URLSearchParams();
    if (filter?.command) q.set("command", filter.command);
    if (filter?.strategy) q.set("strategy", filter.strategy);
    const qs = q.toString();
    return get<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ""}`);
  },
  getRun: (id: string): Promise<RunDetail> => get<RunDetail>(`/api/runs/${id}`),
  getWalkforward: (id: string): Promise<WalkForwardResponse> =>
    get<WalkForwardResponse>(`/api/walkforward/${id}`),
  getEvaluate: (id: string): Promise<EvaluateResponse> =>
    get<EvaluateResponse>(`/api/evaluate/${id}`),
  getStrategies: (): Promise<StrategyInfo[]> => get<StrategyInfo[]>("/api/strategies"),
  searchProducts: (q: string, limit = 20): Promise<ProductHit[]> =>
    get<ProductHit[]>(`/api/products?q=${encodeURIComponent(q)}&limit=${limit}`),
  triggerBacktest: (req: BacktestRequest): Promise<JobStatus> =>
    post<JobStatus>("/api/backtest", req),
  listJobs: (): Promise<JobStatus[]> => get<JobStatus[]>("/api/jobs"),
  getBacktest: (id: string): Promise<BacktestDetail> =>
    get<BacktestDetail>(`/api/backtest/${id}`),
  getFills: (id: string, offset = 0, limit = 100): Promise<FillsPage> =>
    get<FillsPage>(`/api/backtest/${id}/fills?offset=${offset}&limit=${limit}`),
};
