ALTER TABLE runs ADD COLUMN IF NOT EXISTS seed INTEGER NOT NULL DEFAULT 42;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS trials INTEGER NOT NULL DEFAULT 1;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS deterministic BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE runs SET deterministic = FALSE WHERE kind = 'imported';

CREATE TABLE IF NOT EXISTS trial_results (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    trial INTEGER NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    actual_json TEXT NOT NULL,
    failures_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, case_key, trial),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_trials_run_case
ON trial_results(run_id, case_key, trial);
