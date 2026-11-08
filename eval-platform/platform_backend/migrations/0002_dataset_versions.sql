CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_id TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    case_count INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(dataset_id, version_hash),
    FOREIGN KEY(dataset_id) REFERENCES datasets(id)
);

ALTER TABLE cases ADD COLUMN IF NOT EXISTS version_hash TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS dataset_version_hash TEXT;

INSERT INTO dataset_versions(dataset_id, version_hash, case_count, source_path, created_at)
SELECT id, source_hash, case_count, source_path, created_at
FROM datasets
ON CONFLICT (dataset_id, version_hash) DO NOTHING;

UPDATE cases
SET version_hash = datasets.source_hash
FROM datasets
WHERE cases.dataset_id = datasets.id
  AND cases.version_hash IS NULL;

UPDATE runs
SET dataset_version_hash = datasets.source_hash
FROM datasets
WHERE runs.dataset_id = datasets.id
  AND runs.dataset_version_hash IS NULL;

ALTER TABLE cases ALTER COLUMN version_hash SET NOT NULL;
ALTER TABLE runs ALTER COLUMN dataset_version_hash SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cases_dataset_version
ON cases(dataset_id, version_hash);

CREATE INDEX IF NOT EXISTS idx_runs_dataset_version
ON runs(dataset_id, dataset_version_hash);
