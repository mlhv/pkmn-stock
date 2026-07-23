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

export interface StrategyInfo { name: string; thesis: string; }
