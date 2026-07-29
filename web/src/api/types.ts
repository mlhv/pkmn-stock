export interface RunSummary {
  run_id: string;
  recorded_at: string;
  command: string;
  strategy: string;
  git_sha: string | null;
  git_dirty: boolean;
  results: Record<string, number>;
}

export interface RunDetail extends RunSummary {
  config_hash: string;
  config: Record<string, unknown>;
  data_fingerprint: Record<string, unknown>;
  runtime: Record<string, unknown> | null;
}

export interface EquityPoint { date: string; equity: number; }

export interface FoldRow {
  is_start: string; is_end: string; oos_start: string; oos_end: string;
  params: Record<string, unknown>;
  is_summary: Record<string, number>;
  oos_summary: Record<string, number>;
}

export interface RigorCI {
  point: number; lo: number; hi: number; level: number;
  n_boot: number; mean_block: number; seed: number;
}

export interface WalkForwardResponse {
  run_id: string; strategy: string;
  summary: Record<string, number>;
  folds: FoldRow[];
  rigor: RigorCI | null;
  equity_curve: EquityPoint[];
}

export interface ConfidenceInterval { point: number; lo: number; hi: number; level: number; }

export interface StrategyStat {
  strategy: string; total_return: number; ci: ConfidenceInterval;
  sharpe: number; dsr: number | null;
}

export interface EvaluateResponse {
  run_id: string; reality_check_p: number; benchmark: string;
  n_days: number; start: string; end: string;
  params: Record<string, unknown>;
  strategies: StrategyStat[];
}

export interface ParamSpecInfo {
  name: string; kind: "int" | "float"; default: number;
  low: number; high: number; log: boolean;
}

export interface StrategyInfo { name: string; thesis: string; params: ParamSpecInfo[]; }

export interface ProductHit {
  product_id: number; name: string; kind: string;
  sub_types: string[]; last_priced_on: string;
}

export interface HoldingInput {
  product_id: number; sub_type: string; quantity: number;
  avg_cost: number; opened_on: string;
}

export interface BacktestRequest {
  strategy: string; start: string; end: string;
  params: Record<string, number>;
  cash: number; impact: boolean; kind: string;
  holdings: HoldingInput[];
}

export interface JobStatus {
  job_id: string; status: string; request: Record<string, unknown>;
  submitted_at: string; started_at: string | null; finished_at: string | null;
  run_id: string | null; benchmark_run_id: string | null; error: string | null;
}

export interface FillRow {
  day: string; product_id: number; sub_type: string; quantity: number;
  price: number; fees: number; impact: number; seeded: boolean;
}

export interface FillsPage {
  run_id: string; offset: number; limit: number; n_fills: number; fills: FillRow[];
}

export interface BenchmarkCurve {
  run_id: string; strategy: string; equity_curve: EquityPoint[];
}

export interface BacktestDetail {
  run_id: string; strategy: string;
  config: Record<string, unknown>;
  summary: Record<string, number>;
  equity_curve: EquityPoint[];
  benchmark: BenchmarkCurve | null;
  n_fills: number;
  seeded_exits: string[];
}
