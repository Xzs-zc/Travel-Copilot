ALTER TABLE case_results
ADD COLUMN IF NOT EXISTS attribution_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE case_results
ADD COLUMN IF NOT EXISTS input_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE human_labels
ADD COLUMN IF NOT EXISTS corrected_cause TEXT NOT NULL DEFAULT '';

UPDATE case_results cr
SET input_json = (
    SELECT c.input_json::jsonb
    FROM runs r
    JOIN cases c
      ON c.dataset_id = r.dataset_id
     AND c.case_key = cr.case_key
    WHERE r.id = cr.run_id
    ORDER BY (c.version_hash = r.dataset_version_hash) DESC
    LIMIT 1
)
WHERE cr.input_json = '{}'::jsonb
  AND EXISTS (
    SELECT 1
    FROM runs r
    JOIN cases c
      ON c.dataset_id = r.dataset_id
     AND c.case_key = cr.case_key
    WHERE r.id = cr.run_id
  );
