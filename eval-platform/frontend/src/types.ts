export type Dataset = {
  id: string;
  name: string;
  version: string;
  description: string;
  source_path: string;
  source_hash: string;
  case_count: number;
  created_at: string;
  categories?: Record<string, number>;
  versions?: Array<{
    version_hash: string;
    version_hash_short: string;
    case_count: number;
    created_at: string;
  }>;
};

export type DatasetDiff = {
  summary: {
    added: number;
    deleted: number;
    modified: number;
    unchanged: number;
  };
  added: string[];
  deleted: string[];
  modified: string[];
};

export type EvalCase = {
  id: string;
  dataset_id: string;
  case_key: string;
  name: string;
  category: string;
  severity: string;
  difficulty: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  tags: string[];
};

export type RunMetrics = {
  total?: number;
  passed?: number;
  failed?: number;
  pass_rate?: number;
  critical_failures?: number;
  avg_duration_ms?: number;
  p95_duration_ms?: number;
  recall_at_5?: number;
  mrr_at_5?: number;
  ndcg_at_5?: number;
  trials?: number;
  seed?: number;
  deterministic?: boolean;
  variance_note?: string;
  categories?: Record<
    string,
    { total: number; passed: number; pass_rate: number; avg_duration_ms: number }
  >;
};

export type Run = {
  id: string;
  dataset_id: string;
  name: string;
  variant: string;
  system_config_id: string;
  kind: "executed" | "imported";
  seed: number;
  trials: number;
  deterministic: boolean;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  baseline_run_id: string | null;
  metrics: RunMetrics;
  root_causes: Record<string, number>;
  config: Record<string, unknown>;
  started_at: string;
  finished_at?: string;
  duration_ms: number;
  error: string;
  case_counts?: Record<string, number>;
};

export type SystemConfigRecord = {
  id: string;
  name: string;
  description: string;
  config: {
    rag_mode: string;
    policy_gate: boolean;
    validators: string;
    prompt_version: string;
    model: string;
  };
};

export type LabelingItem = {
  run_id: string;
  run_name: string;
  case_key: string;
  name: string;
  status: string;
  question: string;
  answer: string;
  citations: unknown[];
  retrieved: Array<{ id?: string; title?: string; section?: string; content?: string }>;
  gold_sections: unknown[];
  failures: string[];
};

export type FlakyCase = {
  case_key: string;
  trials: number;
  passed_trials: number;
  score_variance: number;
  statuses: string[];
  scores: number[];
};

export type FailureCluster = {
  category: string;
  root_cause: string;
  count: number;
  representative_cases: Array<{
    case_key: string;
    name: string;
    failures: string[];
    attribution: {
      primary?: string;
      secondary?: string[];
      method?: string;
      rule_id?: string;
      evidence?: Record<string, unknown>;
    };
  }>;
  methods: string[];
  rule_ids: string[];
};

export type FailureReview = {
  reviewed: number;
  correct: number;
  changed: number;
  dataset_errors: number;
  agreement_rate: number | null;
  insufficient: boolean;
  minimum_reviewed: number;
  by_primary: Record<string, { reviewed: number; correct: number; agreement_rate: number | null }>;
  confusion: Record<string, Record<string, number>>;
};

export type CaseResult = {
  id: number;
  run_id: string;
  case_key: string;
  name: string;
  category: string;
  severity: string;
  status: "passed" | "failed";
  score: number;
  duration_ms: number;
  trace_id: string;
  actual: Record<string, unknown>;
  expected: Record<string, unknown>;
  failures: string[];
  root_causes: string[];
  metrics: Record<string, unknown>;
};

export type TraceSpan = {
  id: number;
  run_id: string;
  case_key: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  span_type: string;
  status: string;
  start_ms: number;
  duration_ms: number;
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  error_text: string;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  model: string | null;
};

export type CompareResult = {
  base: Run;
  candidate: Run;
  summary: {
    fixed: number;
    regressions: number;
    stable_pass: number;
    stable_fail: number;
    pass_rate_delta: number;
    mcnemar_b: number;
    mcnemar_c: number;
    statistics: {
      sample_size: number;
      warning: string;
      available: boolean;
      reason: string;
      mcnemar_p_value: number | null;
      ci_lower: number | null;
      ci_upper: number | null;
      significant: boolean | null;
    };
  };
  cases: Array<{
    case_key: string;
    name: string;
    category: string;
    base_status: string;
    candidate_status: string;
    delta: "fixed" | "regressed" | "stable_pass" | "stable_fail" | "changed";
    base_duration_ms: number;
    candidate_duration_ms: number;
  }>;
};
