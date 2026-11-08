ALTER TABLE runs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'executed';

CREATE TABLE IF NOT EXISTS imports (
    source_hash TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(source_hash, run_id),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_imports_source_hash
ON imports(source_hash);
