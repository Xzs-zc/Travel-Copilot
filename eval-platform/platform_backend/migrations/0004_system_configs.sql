CREATE TABLE IF NOT EXISTS system_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO system_configs(id, name, description, config_json, created_at)
VALUES
    (
        'baseline-no-rag',
        'Baseline · 无 RAG',
        '关闭知识检索，其余业务规则与当前配置一致。',
        '{"id":"baseline-no-rag","name":"Baseline · 无 RAG","description":"关闭知识检索，其余业务规则与当前配置一致。","rag_mode":"off","policy_gate":true,"validators":"strict","prompt_version":"traveling-current-v1","model":"deepseek-chat"}',
        '2026-11-07T00:00:00+00:00'
    ),
    (
        'baseline-basic-validators',
        'Baseline · 基础校验',
        '仅检查输入与结果的基本结构，不执行严格契约校验。',
        '{"id":"baseline-basic-validators","name":"Baseline · 基础校验","description":"仅检查输入与结果的基本结构，不执行严格契约校验。","rag_mode":"hybrid","policy_gate":true,"validators":"basic","prompt_version":"traveling-current-v1","model":"deepseek-chat"}',
        '2026-11-07T00:00:00+00:00'
    ),
    (
        'candidate-current',
        'Candidate · 当前配置',
        '当前 Traveling 的严格校验、安全边界与混合检索配置。',
        '{"id":"candidate-current","name":"Candidate · 当前配置","description":"当前 Traveling 的严格校验、安全边界与混合检索配置。","rag_mode":"hybrid","policy_gate":true,"validators":"strict","prompt_version":"traveling-current-v1","model":"deepseek-chat"}',
        '2026-11-07T00:00:00+00:00'
    )
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    config_json = EXCLUDED.config_json;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS system_config_id TEXT;

UPDATE runs
SET system_config_id = CASE
    WHEN variant = 'legacy' THEN 'baseline-no-rag'
    WHEN variant IN ('baseline-no-rag', 'baseline-basic-validators', 'candidate-current')
        THEN variant
    ELSE 'candidate-current'
END
WHERE system_config_id IS NULL;

ALTER TABLE runs ALTER COLUMN system_config_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_runs_system_config
ON runs(system_config_id, started_at DESC);
