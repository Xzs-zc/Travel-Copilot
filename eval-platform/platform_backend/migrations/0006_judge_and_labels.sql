CREATE TABLE IF NOT EXISTS judgments (
    id BIGSERIAL PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    rubric_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    sample_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    error_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_labels (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    dimension TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    label_score INTEGER NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    labeler TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, case_key, dimension, labeler),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_human_labels_run
ON human_labels(run_id, case_key);

CREATE TABLE IF NOT EXISTS judge_bias_results (
    id BIGSERIAL PRIMARY KEY,
    check_type TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    metric_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
