CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    case_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    input_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    FOREIGN KEY(dataset_id) REFERENCES datasets(id)
);
CREATE INDEX IF NOT EXISTS idx_cases_dataset ON cases(dataset_id);
CREATE INDEX IF NOT EXISTS idx_cases_category ON cases(category);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    name TEXT NOT NULL,
    variant TEXT NOT NULL,
    status TEXT NOT NULL,
    baseline_run_id TEXT,
    config_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    root_causes_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(dataset_id) REFERENCES datasets(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_dataset ON runs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS case_results (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    actual_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    failures_json TEXT NOT NULL,
    root_causes_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_case_run ON case_results(run_id, case_key);
CREATE INDEX IF NOT EXISTS idx_case_status ON case_results(run_id, status);

CREATE TABLE IF NOT EXISTS trace_spans (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    span_type TEXT NOT NULL,
    status TEXT NOT NULL,
    start_ms REAL NOT NULL,
    duration_ms REAL NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    seq INTEGER NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_trace_case ON trace_spans(run_id, case_key, seq);
