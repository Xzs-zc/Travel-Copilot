from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import DATABASE_URL, MANIFEST_PATH, ensure_dirs
from .core.attribution import Attribution, attribute_failure
from .core.failure_taxonomy import CATEGORY_DEFAULT_CAUSE
from .core.system_config import BUILTIN_SYSTEM_CONFIGS, SystemConfig
from .core.stats import mcnemar, paired_bootstrap_ci


_WRITE_LOCK = threading.RLock()
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    ensure_dirs()
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["filename"]
            for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(filename, applied_at) VALUES (%s, %s)",
                (path.name, utcnow()),
            )


def seed_dataset_from_jsonl(
    dataset_id: str = "traveling-business-v1",
    *,
    dataset_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    from .config import DATASET_PATH

    dataset_path = dataset_path or DATASET_PATH
    manifest_path = manifest_path or MANIFEST_PATH
    rows = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    created_at = utcnow()
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO datasets(id, name, version, description, source_path, source_hash, case_count, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                version = EXCLUDED.version,
                description = EXCLUDED.description,
                source_path = EXCLUDED.source_path,
                source_hash = EXCLUDED.source_hash,
                case_count = EXCLUDED.case_count
            """,
            (
                dataset_id,
                manifest["name"],
                manifest["version"],
                manifest["description"],
                str(dataset_path),
                source_hash,
                len(rows),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO dataset_versions(dataset_id, version_hash, case_count, source_path, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id, version_hash) DO NOTHING
            """,
            (dataset_id, source_hash, len(rows), str(dataset_path), created_at),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO cases(
                    id, dataset_id, case_key, name, category, severity, difficulty,
                    input_json, expected_json, tags_json, version_hash, source, rationale, split
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    dataset_id = EXCLUDED.dataset_id,
                    case_key = EXCLUDED.case_key,
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    severity = EXCLUDED.severity,
                    difficulty = EXCLUDED.difficulty,
                    input_json = EXCLUDED.input_json,
                    expected_json = EXCLUDED.expected_json,
                    tags_json = EXCLUDED.tags_json,
                    version_hash = EXCLUDED.version_hash,
                    source = EXCLUDED.source,
                    rationale = EXCLUDED.rationale,
                    split = EXCLUDED.split
                """,
                [
                    (
                        f"{dataset_id}:{source_hash}:{row['case_key']}",
                        dataset_id,
                        row["case_key"],
                        row["name"],
                        row["category"],
                        row.get("severity", "medium"),
                        row.get("difficulty", "medium"),
                        _json(row["input"]),
                        _json(row["expected"]),
                        _json(row.get("tags", [])),
                        source_hash,
                        row.get("source", "adapted"),
                        row.get("rationale", ""),
                        row.get("split", "all"),
                    )
                    for row in rows
                ],
            )
    return get_dataset(dataset_id) or {}


def seed_system_configs(configs: dict[str, SystemConfig] | None = None) -> list[dict[str, Any]]:
    values = configs or BUILTIN_SYSTEM_CONFIGS
    created_at = utcnow()
    with _WRITE_LOCK, connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO system_configs(id, name, description, config_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    config_json = EXCLUDED.config_json
                """,
                [
                    (
                        config.id,
                        config.name,
                        config.description,
                        _json(config.as_record()),
                        created_at,
                    )
                    for config in values.values()
                ],
            )
    return list_system_configs()


def upsert_system_config(config: SystemConfig) -> dict[str, Any]:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO system_configs(id, name, description, config_json, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                config_json = EXCLUDED.config_json
            """,
            (
                config.id,
                config.name,
                config.description,
                _json(config.as_record()),
                utcnow(),
            ),
        )
    row = next(
        (
            item
            for item in list_system_configs()
            if item["id"] == config.id
        ),
        None,
    )
    return row or {}


def list_system_configs() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM system_configs ORDER BY id"
        ).fetchall()
    return [
        {
            **{key: value for key, value in dict(row).items() if key != "config_json"},
            "config": _loads(row["config_json"], {}),
        }
        for row in rows
    ]


def get_system_config(config_id: str) -> SystemConfig | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT config_json FROM system_configs WHERE id = %s",
            (config_id,),
        ).fetchone()
    if row is None:
        return None
    return SystemConfig.model_validate(_loads(row["config_json"], {}))


def create_imported_dataset(
    *,
    dataset_id: str,
    name: str,
    description: str,
    source_path: str,
    version_hash: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    created_at = utcnow()
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO datasets(
                id, name, version, description, source_path, source_hash, case_count, created_at
            )
            VALUES (%s, %s, 'imported', %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                version = EXCLUDED.version,
                description = EXCLUDED.description,
                source_path = EXCLUDED.source_path,
                source_hash = EXCLUDED.source_hash,
                case_count = EXCLUDED.case_count
            """,
            (dataset_id, name, description, source_path, version_hash, len(rows), created_at),
        )
        conn.execute(
            """
            INSERT INTO dataset_versions(
                dataset_id, version_hash, case_count, source_path, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id, version_hash) DO NOTHING
            """,
            (dataset_id, version_hash, len(rows), source_path, created_at),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO cases(
                    id, dataset_id, case_key, name, category, severity, difficulty,
                    input_json, expected_json, tags_json, version_hash, source, rationale, split
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    severity = EXCLUDED.severity,
                    difficulty = EXCLUDED.difficulty,
                    input_json = EXCLUDED.input_json,
                    expected_json = EXCLUDED.expected_json,
                    tags_json = EXCLUDED.tags_json,
                    source = EXCLUDED.source,
                    rationale = EXCLUDED.rationale,
                    split = EXCLUDED.split
                """,
                [
                    (
                        f"{dataset_id}:{version_hash}:{row['case_key']}",
                        dataset_id,
                        row["case_key"],
                        row["name"],
                        row["category"],
                        row.get("severity", "high"),
                        row.get("difficulty", "medium"),
                        _json(row["input"]),
                        _json(row["expected"]),
                        _json(row.get("tags", [])),
                        version_hash,
                        row.get("source", "adapted"),
                        row.get("rationale", ""),
                        row.get("split", "all"),
                    )
                    for row in rows
                ],
            )
    return get_dataset(dataset_id) or {}


def create_imported_run(
    *,
    run_id: str,
    dataset_id: str,
    dataset_version_hash: str,
    name: str,
    system_config_id: str,
    config: dict[str, Any],
    metrics: dict[str, Any],
    root_causes: dict[str, int],
    started_at: str,
    finished_at: str,
    duration_ms: int,
    deterministic: bool = False,
) -> dict[str, Any]:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO runs(
                id, dataset_id, dataset_version_hash, name, variant, system_config_id,
                status, baseline_run_id, config_json, metrics_json, root_causes_json,
                started_at, finished_at, duration_ms, error, kind, deterministic, trials, seed
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, 'completed', NULL, %s, %s, %s,
                %s, %s, %s, '', 'imported', %s, 1, 42
            )
            ON CONFLICT (id) DO UPDATE SET
                dataset_id = EXCLUDED.dataset_id,
                dataset_version_hash = EXCLUDED.dataset_version_hash,
                name = EXCLUDED.name,
                variant = EXCLUDED.variant,
                system_config_id = EXCLUDED.system_config_id,
                status = EXCLUDED.status,
                config_json = EXCLUDED.config_json,
                metrics_json = EXCLUDED.metrics_json,
                root_causes_json = EXCLUDED.root_causes_json,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                duration_ms = EXCLUDED.duration_ms,
                error = EXCLUDED.error,
                kind = EXCLUDED.kind,
                deterministic = EXCLUDED.deterministic,
                trials = EXCLUDED.trials,
                seed = EXCLUDED.seed
            """,
            (
                run_id,
                dataset_id,
                dataset_version_hash,
                name,
                system_config_id,
                system_config_id,
                _json(config),
                _json(metrics),
                _json(root_causes),
                started_at,
                finished_at,
                duration_ms,
                deterministic,
            ),
        )
    return get_run(run_id) or {}


def import_exists(
    source_hash: str,
    run_id: str,
    importer_version: str | None = None,
) -> bool:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT importer_version
            FROM imports
            WHERE source_hash = %s AND run_id = %s
            """,
            (source_hash, run_id),
        ).fetchone()
    if row is None:
        return False
    return (
        importer_version is None
        or row["importer_version"] == importer_version
    )


def record_import(
    *,
    source_hash: str,
    run_id: str,
    source_path: str,
    importer_version: str,
) -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO imports(source_hash, run_id, source_path, importer_version, imported_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_hash, run_id) DO UPDATE SET
                source_path = EXCLUDED.source_path,
                importer_version = EXCLUDED.importer_version,
                imported_at = EXCLUDED.imported_at
            """,
            (source_hash, run_id, source_path, importer_version, utcnow()),
        )


def _dataset_row(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["source_hash"] = data["source_hash"][:12]
    return data


def list_datasets() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
        return [_dataset_row(row) for row in rows]


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = %s", (dataset_id,)).fetchone()
        if row is None:
            return None
        data = _dataset_row(row)
        versions = conn.execute(
            """
            SELECT version_hash, case_count, created_at
            FROM dataset_versions
            WHERE dataset_id = %s
            ORDER BY created_at DESC
            """,
            (dataset_id,),
        ).fetchall()
        data["versions"] = [
            {
                **dict(dict(item)),
                "version_hash_short": item["version_hash"][:12],
            }
            for item in versions
        ]
        categories = conn.execute(
            """
            SELECT category, COUNT(*) AS count
            FROM cases
            WHERE dataset_id = %s AND version_hash = %s
            GROUP BY category
            ORDER BY category
            """,
            (dataset_id, row["source_hash"]),
        ).fetchall()
        data["categories"] = {item["category"]: item["count"] for item in categories}
        return data


def get_cases(dataset_id: str, version_hash: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        if version_hash is None:
            row = conn.execute(
                "SELECT source_hash FROM datasets WHERE id = %s", (dataset_id,)
            ).fetchone()
            if row is None:
                return []
            version_hash = row["source_hash"]
        rows = conn.execute(
            """
            SELECT * FROM cases
            WHERE dataset_id = %s AND version_hash = %s
            ORDER BY case_key
            """,
            (dataset_id, version_hash),
        ).fetchall()
    return [
        {
            **dict(row),
            "input": _loads(row["input_json"], {}),
            "expected": _loads(row["expected_json"], {}),
            "tags": _loads(row["tags_json"], []),
        }
        for row in rows
    ]


def diff_dataset_versions(
    dataset_id: str,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    before = {
        item["case_key"]: item
        for item in get_cases(dataset_id, from_version)
    }
    after = {
        item["case_key"]: item
        for item in get_cases(dataset_id, to_version)
    }
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(
        key
        for key in set(before) & set(after)
        if (
            before[key]["input"],
            before[key]["expected"],
            before[key]["name"],
            before[key]["tags"],
        )
        != (
            after[key]["input"],
            after[key]["expected"],
            after[key]["name"],
            after[key]["tags"],
        )
    )
    return {
        "dataset_id": dataset_id,
        "from_version": from_version,
        "to_version": to_version,
        "summary": {
            "added": len(added),
            "deleted": len(deleted),
            "modified": len(modified),
            "unchanged": len(set(before) & set(after)) - len(modified),
        },
        "added": added,
        "deleted": deleted,
        "modified": modified,
    }


def create_run(
    *,
    dataset_id: str,
    name: str,
    variant: str,
    system_config_id: str | None = None,
    baseline_run_id: str | None = None,
    config: dict[str, Any] | None = None,
    seed: int = 42,
    trials: int = 1,
    split: str = "all",
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:16]
    config_id = system_config_id or variant
    payload = {
        "dataset_id": dataset_id,
        "system_config_id": config_id,
        "seed": seed,
        "trials": trials,
        "split": split,
        **(config or {}),
    }
    with _WRITE_LOCK, connection() as conn:
        dataset = conn.execute(
            "SELECT source_hash FROM datasets WHERE id = %s", (dataset_id,)
        ).fetchone()
        if dataset is None:
            raise ValueError("dataset_not_found")
        system_config = conn.execute(
            "SELECT id FROM system_configs WHERE id = %s",
            (config_id,),
        ).fetchone()
        if system_config is None:
            raise ValueError("system_config_not_found")
        conn.execute(
            """
            INSERT INTO runs(
                id, dataset_id, dataset_version_hash, name, variant, system_config_id, status,
                baseline_run_id, config_json, metrics_json, root_causes_json, started_at,
                seed, trials, deterministic, split
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s, '{}', '{}', %s, %s, %s, TRUE, %s)
            """,
            (
                run_id,
                dataset_id,
                dataset["source_hash"],
                name,
                config_id,
                config_id,
                baseline_run_id,
                _json(payload),
                utcnow(),
                seed,
                trials,
                split,
            ),
        )
    return get_run(run_id) or {}


def update_run_status(run_id: str, status: str, *, error: str = "") -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            "UPDATE runs SET status = %s, error = %s WHERE id = %s",
            (status, error[:1000], run_id),
        )


def interrupt_incomplete_runs() -> int:
    with _WRITE_LOCK, connection() as conn:
        rows = conn.execute(
            "SELECT id FROM runs WHERE status IN ('queued', 'running')"
        ).fetchall()
        if not rows:
            return 0
        conn.execute(
            """
            UPDATE runs
            SET status = 'interrupted', finished_at = %s,
                error = CASE WHEN error = '' THEN 'platform_restarted' ELSE error END
            WHERE status IN ('queued', 'running')
            """,
            (utcnow(),),
        )
        return len(rows)


def finish_run(
    run_id: str,
    *,
    metrics: dict[str, Any],
    root_causes: dict[str, Any],
    duration_ms: int,
    deterministic: bool = True,
) -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            UPDATE runs SET status = 'completed', metrics_json = %s, root_causes_json = %s,
                finished_at = %s, duration_ms = %s, error = '', deterministic = %s WHERE id = %s
            """,
            (_json(metrics), _json(root_causes), utcnow(), duration_ms, deterministic, run_id),
        )


def fail_run(run_id: str, error: str, duration_ms: int) -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            UPDATE runs SET status = 'failed', error = %s, finished_at = %s,
                duration_ms = %s WHERE id = %s
            """,
            (error[:2000], utcnow(), duration_ms, run_id),
        )


def insert_case_result(run_id: str, case: dict[str, Any], outcome: dict[str, Any]) -> None:
    metrics = outcome["metrics"]
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO case_results(
                run_id, case_key, name, category, severity, status, score, duration_ms,
                trace_id, actual_json, expected_json, failures_json, root_causes_json,
                metrics_json, tokens_total, cost_usd, tool_calls, steps, attribution_json,
                input_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, case_key) DO UPDATE SET
                name = EXCLUDED.name,
                category = EXCLUDED.category,
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                score = EXCLUDED.score,
                duration_ms = EXCLUDED.duration_ms,
                trace_id = EXCLUDED.trace_id,
                actual_json = EXCLUDED.actual_json,
                expected_json = EXCLUDED.expected_json,
                failures_json = EXCLUDED.failures_json,
                root_causes_json = EXCLUDED.root_causes_json,
                metrics_json = EXCLUDED.metrics_json,
                tokens_total = EXCLUDED.tokens_total,
                cost_usd = EXCLUDED.cost_usd,
                tool_calls = EXCLUDED.tool_calls,
                steps = EXCLUDED.steps,
                attribution_json = EXCLUDED.attribution_json,
                input_json = EXCLUDED.input_json
            """,
            (
                run_id,
                case["case_key"],
                case["name"],
                case["category"],
                case["severity"],
                "passed" if outcome["passed"] else "failed",
                100.0 if outcome["passed"] else 0.0,
                outcome["duration_ms"],
                outcome["trace_id"],
                _json(outcome["actual"]),
                _json(case["expected"]),
                _json(outcome["failures"]),
                _json(outcome["root_causes"]),
                _json(metrics),
                metrics.get("tokens_total"),
                metrics.get("cost_usd"),
                metrics.get("tool_calls"),
                metrics.get("steps"),
                Jsonb(outcome.get("attribution") or {}),
                Jsonb(case.get("input") or {}),
            ),
        )


def insert_trace_spans(run_id: str, case_key: str, spans: list[dict[str, Any]]) -> None:
    with _WRITE_LOCK, connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO trace_spans(
                    run_id, case_key, trace_id, span_id, parent_span_id, name, span_type,
                    status, start_ms, duration_ms, input_json, output_json, metadata_json, seq,
                    error_text, tokens_in, tokens_out, cost_usd, model
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        run_id,
                        case_key,
                        span["trace_id"],
                        span["span_id"],
                        span.get("parent_span_id"),
                        span["name"],
                        span["span_type"],
                        span["status"],
                        span["start_ms"],
                        span["duration_ms"],
                        _json(span.get("input")),
                        _json(span.get("output")),
                        _json(span.get("metadata", {})),
                        index,
                        str(span.get("error_text") or "")[:2000],
                        span.get("tokens_in"),
                        span.get("tokens_out"),
                        span.get("cost_usd"),
                        span.get("model"),
                    )
                    for index, span in enumerate(spans)
                ],
            )


def insert_trial_result(
    run_id: str,
    case_key: str,
    trial: int,
    outcome: dict[str, Any],
) -> None:
    execution_status = outcome.get("execution_status") or (
        "passed" if outcome["passed"] else "failed"
    )
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO trial_results(
                run_id, case_key, trial, status, score, duration_ms,
                actual_json, failures_json, metrics_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, case_key, trial) DO UPDATE SET
                status = EXCLUDED.status,
                score = EXCLUDED.score,
                duration_ms = EXCLUDED.duration_ms,
                actual_json = EXCLUDED.actual_json,
                failures_json = EXCLUDED.failures_json,
                metrics_json = EXCLUDED.metrics_json
            """,
            (
                run_id,
                case_key,
                trial,
                execution_status,
                outcome["score"],
                outcome["duration_ms"],
                _json(outcome["actual"]),
                _json(outcome["failures"]),
                _json(outcome["metrics"]),
                utcnow(),
            ),
        )


def list_trial_results(
    run_id: str,
    case_key: str = "",
) -> list[dict[str, Any]]:
    clauses = ["run_id = %s"]
    params: list[Any] = [run_id]
    if case_key:
        clauses.append("case_key = %s")
        params.append(case_key)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM trial_results
            WHERE {' AND '.join(clauses)}
            ORDER BY case_key, trial
            """,
            params,
        ).fetchall()
    return [
        {
            **{key: value for key, value in dict(row).items() if not key.endswith("_json")},
            "actual": _loads(row["actual_json"], {}),
            "failures": _loads(row["failures_json"], []),
            "metrics": _loads(row["metrics_json"], {}),
        }
        for row in rows
    ]


def flaky_cases(run_id: str) -> list[dict[str, Any]]:
    rows = list_trial_results(run_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["case_key"], []).append(row)
    result = []
    for case_key, trials in grouped.items():
        valid_trials = [
            item for item in trials if item["status"] != "error"
        ]
        statuses = {item["status"] for item in valid_trials}
        scores = [float(item["score"]) for item in valid_trials]
        if len(statuses) <= 1 and len(set(scores)) <= 1:
            continue
        mean = sum(scores) / len(scores) if scores else 0.0
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        result.append(
            {
                "case_key": case_key,
                "trials": len(trials),
                "passed_trials": sum(item["status"] == "passed" for item in trials),
                "valid_trials": len(valid_trials),
                "error_trials": len(trials) - len(valid_trials),
                "score_variance": round(variance, 6),
                "statuses": [item["status"] for item in trials],
                "scores": scores,
            }
        )
    return sorted(
        result,
        key=lambda item: (-item["score_variance"], item["case_key"]),
    )


def failure_clusters(run_id: str) -> list[dict[str, Any]]:
    failed = list_case_results(run_id, status="failed")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in failed:
        causes = case.get("root_causes") or ["unclassified"]
        for cause in causes:
            grouped.setdefault((case["category"], cause), []).append(case)
    return [
        {
            "category": category,
            "root_cause": cause,
            "count": len(cases),
            "representative_cases": [
                {
                    "case_key": item["case_key"],
                    "name": item["name"],
                    "failures": item["failures"],
                    "attribution": item.get("attribution") or {},
                }
                for item in cases[:5]
            ],
            "methods": sorted({
                str((item.get("attribution") or {}).get("method") or "unknown")
                for item in cases
            }),
            "rule_ids": sorted({
                str((item.get("attribution") or {}).get("rule_id") or "")
                for item in cases
                if (item.get("attribution") or {}).get("rule_id")
            }),
        }
        for (category, cause), cases in sorted(
            grouped.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]


def trend_runs(
    *,
    dataset_id: str,
    system_config_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses = ["dataset_id = %s", "status = 'completed'"]
    params: list[Any] = [dataset_id]
    if system_config_id:
        clauses.append("system_config_id = %s")
        params.append(system_config_id)
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM runs
            WHERE {' AND '.join(clauses)}
            ORDER BY started_at ASC
            LIMIT %s
            """,
            params,
        ).fetchall()
    return [_run_row(row) for row in rows]


def failure_review_consistency(run_id: str) -> dict[str, Any]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT hl.*, cr.attribution_json
            FROM human_labels hl
            JOIN case_results cr
              ON cr.run_id = hl.run_id AND cr.case_key = hl.case_key
            WHERE hl.run_id = %s
              AND hl.dimension = 'failure_attribution'
              AND hl.labeler <> 'test-fixture'
            """,
            (run_id,),
        ).fetchall()
    total = len(rows)
    correct = sum(int(row["label_score"]) == 1 for row in rows)
    changed = sum(int(row["label_score"]) == 2 for row in rows)
    dataset_errors = sum(int(row["label_score"]) == 3 for row in rows)
    by_primary: dict[str, dict[str, Any]] = {}
    confusion: dict[str, dict[str, int]] = {}
    for row in rows:
        attribution = (
            row["attribution_json"]
            if isinstance(row.get("attribution_json"), dict)
            else _loads(row.get("attribution_json"), {})
        )
        primary = str(attribution.get("primary") or "unclassified")
        bucket = by_primary.setdefault(primary, {"reviewed": 0, "correct": 0})
        bucket["reviewed"] += 1
        is_correct = int(row["label_score"]) == 1
        bucket["correct"] += int(is_correct)
        reviewed_as = (
            "correct"
            if is_correct
            else str(row.get("corrected_cause") or "dataset_error")
        )
        confusion.setdefault(primary, {})
        confusion[primary][reviewed_as] = confusion[primary].get(reviewed_as, 0) + 1
    for bucket in by_primary.values():
        bucket["agreement_rate"] = round(
            bucket["correct"] / bucket["reviewed"],
            4,
        ) if bucket["reviewed"] else None
    insufficient = total < 20
    return {
        "reviewed": total,
        "correct": correct,
        "changed": changed,
        "dataset_errors": dataset_errors,
        "agreement_rate": (
            None if insufficient or not total else round(correct / total, 4)
        ),
        "insufficient": insufficient,
        "minimum_reviewed": 20,
        "by_primary": by_primary,
        "confusion": confusion,
    }


def _run_row(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["metrics"] = _loads(data.pop("metrics_json"), {})
    data["root_causes"] = _loads(data.pop("root_causes_json"), {})
    data["config"] = _loads(data.pop("config_json"), {})
    return data


def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT %s", (limit,)
        ).fetchall()
    return [_run_row(row) for row in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
        if row is None:
            return None
        data = _run_row(row)
        counts = conn.execute(
            "SELECT status, COUNT(*) AS count FROM case_results WHERE run_id = %s GROUP BY status",
            (run_id,),
        ).fetchall()
        data["case_counts"] = {item["status"]: item["count"] for item in counts}
        return data


def list_case_results(run_id: str, *, status: str = "", category: str = "") -> list[dict[str, Any]]:
    clauses = ["run_id = %s"]
    params: list[Any] = [run_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if category:
        clauses.append("category = %s")
        params.append(category)
    with connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM case_results WHERE {' AND '.join(clauses)} ORDER BY category, case_key",
            params,
        ).fetchall()
    return [
        {
            **{key: value for key, value in dict(row).items() if not key.endswith("_json")},
            "input": (
                row["input_json"]
                if isinstance(row.get("input_json"), dict)
                else _loads(row.get("input_json"), {})
            ),
            "actual": _loads(row["actual_json"], {}),
            "expected": _loads(row["expected_json"], {}),
            "failures": _loads(row["failures_json"], []),
            "root_causes": _loads(row["root_causes_json"], []),
            "metrics": _loads(row["metrics_json"], {}),
            "attribution": (
                row["attribution_json"]
                if isinstance(row.get("attribution_json"), dict)
                else _loads(row.get("attribution_json"), {})
            ),
        }
        for row in rows
    ]


def get_case_result(run_id: str, case_key: str) -> dict[str, Any] | None:
    rows = list_case_results(run_id)
    return next((item for item in rows if item["case_key"] == case_key), None)


def get_trace(run_id: str, case_key: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trace_spans WHERE run_id = %s AND case_key = %s ORDER BY seq",
            (run_id, case_key),
        ).fetchall()
    return [
        {
            **{key: value for key, value in dict(row).items() if not key.endswith("_json")},
            "input": _loads(row["input_json"], {}),
            "output": _loads(row["output_json"], {}),
            "metadata": _loads(row["metadata_json"], {}),
        }
        for row in rows
    ]


def compare_runs(base_run_id: str, candidate_run_id: str) -> dict[str, Any]:
    base = get_run(base_run_id)
    candidate = get_run(candidate_run_id)
    if base is None or candidate is None:
        raise KeyError("run_not_found")
    base_cases = {item["case_key"]: item for item in list_case_results(base_run_id)}
    candidate_cases = {item["case_key"]: item for item in list_case_results(candidate_run_id)}
    all_keys = sorted(set(base_cases) | set(candidate_cases))
    rows = []
    fixes = regressions = stable_pass = stable_fail = 0
    for key in all_keys:
        before = base_cases.get(key)
        after = candidate_cases.get(key)
        before_status = before["status"] if before else "missing"
        after_status = after["status"] if after else "missing"
        if before_status == "failed" and after_status == "passed":
            delta = "fixed"
            fixes += 1
        elif before_status == "passed" and after_status == "failed":
            delta = "regressed"
            regressions += 1
        elif before_status == "passed" and after_status == "passed":
            delta = "stable_pass"
            stable_pass += 1
        elif before_status == "failed" and after_status == "failed":
            delta = "stable_fail"
            stable_fail += 1
        else:
            delta = "changed"
        rows.append(
            {
                "case_key": key,
                "name": (after or before or {}).get("name", key),
                "category": (after or before or {}).get("category", ""),
                "base_status": before_status,
                "candidate_status": after_status,
                "delta": delta,
                "base_duration_ms": (before or {}).get("duration_ms", 0),
                "candidate_duration_ms": (after or {}).get("duration_ms", 0),
            }
        )
    base_scores = [
        1.0 if (base_cases.get(key) or {}).get("status") == "passed" else 0.0
        for key in all_keys
    ]
    candidate_scores = [
        1.0 if (candidate_cases.get(key) or {}).get("status") == "passed" else 0.0
        for key in all_keys
    ]
    pass_rate_delta = round(
        (
            float(candidate["metrics"].get("pass_rate") or 0)
            - float(base["metrics"].get("pass_rate") or 0)
        ),
        2,
    )
    b = sum(
        before == 1.0 and after == 0.0
        for before, after in zip(base_scores, candidate_scores, strict=True)
    )
    c = sum(
        before == 0.0 and after == 1.0
        for before, after in zip(base_scores, candidate_scores, strict=True)
    )
    deterministic = bool(base.get("deterministic")) and bool(
        candidate.get("deterministic")
    )
    statistics: dict[str, Any] = {
        "sample_size": len(all_keys),
        "warning": "样本量小于 30，统计结论仅作参考" if len(all_keys) < 30 else "",
    }
    if deterministic:
        statistics.update(
            {
                "available": False,
                "reason": "确定性任务不展示虚假的置信区间与显著性",
                "mcnemar_p_value": None,
                "ci_lower": None,
                "ci_upper": None,
                "significant": None,
            }
        )
    else:
        ci = paired_bootstrap_ci(
            base_scores,
            candidate_scores,
            seed=int(candidate.get("seed") or 42),
        )
        p_value = mcnemar(b, c)
        ci_lower, ci_upper = ci if ci else (None, None)
        statistics.update(
            {
                "available": bool(ci),
                "reason": "" if ci else "输入无方差，无法估计置信区间",
                "mcnemar_p_value": round(p_value, 6),
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
                "significant": (
                    p_value < 0.05
                    and ci_lower is not None
                    and ci_upper is not None
                    and (ci_lower > 0 or ci_upper < 0)
                ),
            }
        )
    return {
        "base": base,
        "candidate": candidate,
        "summary": {
            "fixed": fixes,
            "regressions": regressions,
            "stable_pass": stable_pass,
            "stable_fail": stable_fail,
            "pass_rate_delta": pass_rate_delta,
            "mcnemar_b": b,
            "mcnemar_c": c,
            "statistics": statistics,
        },
        "cases": rows,
    }


def database_health() -> bool:
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except psycopg.Error:
        return False


def backup_case_results_once() -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_results_backup_20261107
            AS TABLE case_results
            """
        )


def _attribution_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        if item["status"] != "failed":
            continue
        attribution = item.get("attribution") or {}
        primary = str(
            attribution.get("primary")
            or (item.get("root_causes") or ["unclassified"])[0]
        )
        counts[primary] = counts.get(primary, 0) + 1
    return dict(sorted(counts.items()))


def reattribute_run(run_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise KeyError("run_not_found")
    results = list_case_results(run_id)
    before = _attribution_counts(results)
    updated: list[tuple[dict[str, Any], Attribution]] = []
    for result in results:
        if result["status"] != "failed":
            continue
        input_data = result.get("input") or {}
        if not input_data.get("type"):
            fallback = CATEGORY_DEFAULT_CAUSE.get(result["category"])
            attribution = Attribution(
                primary=fallback.value if fallback else "unclassified",
                evidence={"source_root_causes": result.get("root_causes") or []},
                method="category_fallback",
                rule_id="import_without_detail",
            )
        else:
            case = {
                "case_key": result["case_key"],
                "name": result["name"],
                "category": result["category"],
                "input": input_data,
                "expected": result.get("expected") or {},
            }
            attribution = attribute_failure(
                case,
                result.get("actual") or {},
                source_root_causes=result.get("root_causes") or [],
            )
        result["attribution"] = attribution.model_dump(mode="json")
        result["root_causes"] = [
            attribution.primary,
            *attribution.secondary,
        ]
        updated.append((result, attribution))
    after = _attribution_counts(results)
    failed = [item for item in results if item["status"] == "failed"]
    covered = sum(
        (item.get("attribution") or {}).get("method")
        not in {None, "category_fallback"}
        for item in failed
    )
    unclassified = sum(
        (item.get("attribution") or {}).get("primary") == "unclassified"
        for item in failed
    )
    summary = {
        "run_id": run_id,
        "dry_run": dry_run,
        "failed_cases": len(failed),
        "before": before,
        "after": after,
        "attribution_coverage": (
            round(covered / len(failed), 4)
            if failed
            else 1.0
        ),
        "unclassified_rate": (
            round(unclassified / len(failed), 4)
            if failed
            else 0.0
        ),
    }
    if dry_run or not updated:
        return summary
    backup_case_results_once()
    with _WRITE_LOCK, connection() as conn:
        for result, attribution in updated:
            conn.execute(
                """
                UPDATE case_results
                SET attribution_json = %s,
                    root_causes_json = %s
                WHERE run_id = %s AND case_key = %s
                """,
                (
                    Jsonb(attribution.model_dump(mode="json")),
                    _json([attribution.primary, *attribution.secondary]),
                    run_id,
                    result["case_key"],
                ),
            )
        row = conn.execute(
            "SELECT metrics_json FROM runs WHERE id = %s",
            (run_id,),
        ).fetchone()
        metrics = _loads(row["metrics_json"] if row else None, {})
        metrics["attribution_coverage"] = summary["attribution_coverage"]
        metrics["unclassified_rate"] = summary["unclassified_rate"]
        conn.execute(
            "UPDATE runs SET metrics_json = %s, root_causes_json = %s WHERE id = %s",
            (_json(metrics), _json(after), run_id),
        )
    return summary


def get_judgment(cache_key: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM judgments WHERE cache_key = %s",
            (cache_key,),
        ).fetchone()
    if row is None:
        return None
    return {
        **{key: value for key, value in dict(row).items() if not key.endswith("_json")},
        "result": _loads(row["result_json"], {}),
        "usage": _loads(row["usage_json"], {}),
    }


def save_judgment(
    *,
    cache_key: str,
    rubric_version: str,
    judge_model: str,
    prompt_hash: str,
    sample_hash: str,
    output_hash: str,
    status: str,
    result: dict[str, Any],
    usage: dict[str, Any],
    error_text: str = "",
) -> dict[str, Any]:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO judgments(
                cache_key, rubric_version, judge_model, prompt_hash, sample_hash,
                output_hash, status, result_json, usage_json, error_text, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                status = EXCLUDED.status,
                result_json = EXCLUDED.result_json,
                usage_json = EXCLUDED.usage_json,
                error_text = EXCLUDED.error_text,
                created_at = EXCLUDED.created_at
            """,
            (
                cache_key,
                rubric_version,
                judge_model,
                prompt_hash,
                sample_hash,
                output_hash,
                status,
                _json(result),
                _json(usage),
                error_text[:2000],
                utcnow(),
            ),
        )
    return get_judgment(cache_key) or {}


def labeling_candidates(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT cr.*, r.name AS run_name
            FROM case_results cr
            JOIN runs r ON r.id = cr.run_id
            WHERE r.kind = 'imported' AND cr.category = 'knowledge_retrieval'
            ORDER BY cr.status, cr.id
            """
        ).fetchall()
    candidates = []
    for row in rows:
        actual = _loads(row["actual_json"], {})
        if not str(actual.get("answer") or "").strip():
            continue
        expected = _loads(row["expected_json"], {})
        candidates.append(
            {
                "run_id": row["run_id"],
                "run_name": row["run_name"],
                "case_key": row["case_key"],
                "name": row["name"],
                "status": row["status"],
                "question": expected.get("question") or expected.get("message") or row["name"],
                "answer": actual.get("answer") or "",
                "citations": actual.get("citations") or [],
                "retrieved": actual.get("retrieved") or [],
                "gold_sections": expected.get("gold_sections") or [],
                "failures": _loads(row["failures_json"], []),
            }
        )
    passed = [item for item in candidates if item["status"] == "passed"]
    failed = [item for item in candidates if item["status"] == "failed"]
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and (passed or failed):
        if passed:
            selected.append(passed.pop(0))
        if len(selected) < limit and failed:
            selected.append(failed.pop(0))
    return selected[:limit]


def upsert_human_label(
    *,
    run_id: str,
    case_key: str,
    dimension: str,
    rubric_version: str,
    score: int,
    rationale: str,
    labeler: str,
    corrected_cause: str = "",
) -> dict[str, Any]:
    now = utcnow()
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO human_labels(
                run_id, case_key, dimension, rubric_version, label_score,
                rationale, labeler, corrected_cause, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, case_key, dimension, labeler) DO UPDATE SET
                rubric_version = EXCLUDED.rubric_version,
                label_score = EXCLUDED.label_score,
                rationale = EXCLUDED.rationale,
                corrected_cause = EXCLUDED.corrected_cause,
                updated_at = EXCLUDED.updated_at
            """,
            (
                run_id,
                case_key,
                dimension,
                rubric_version,
                score,
                rationale,
                labeler,
                corrected_cause,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM human_labels
            WHERE run_id = %s AND case_key = %s AND dimension = %s AND labeler = %s
            """,
            (run_id, case_key, dimension, labeler),
        ).fetchone()
    return dict(row) if row else {}


def list_human_labels(
    *,
    run_id: str = "",
    labeler: str = "",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = %s")
        params.append(run_id)
    if labeler:
        clauses.append("labeler = %s")
        params.append(labeler)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM human_labels {where} ORDER BY run_id, case_key, dimension",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def label_pool_for_run(
    run_id: str,
    *,
    limit: int = 50,
    stratify: str = "difficulty",
) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT cr.*, c.difficulty
            FROM case_results cr
            JOIN runs r ON r.id = cr.run_id
            JOIN cases c
              ON c.dataset_id = r.dataset_id
             AND c.version_hash = r.dataset_version_hash
             AND c.case_key = cr.case_key
            WHERE cr.run_id = %s
            ORDER BY c.difficulty, cr.case_key
            """,
            (run_id,),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        actual = _loads(row["actual_json"], {})
        expected = _loads(row["expected_json"], {})
        key = str(row.get(stratify) or "unknown")
        grouped.setdefault(key, []).append(
            {
                "run_id": run_id,
                "case_key": row["case_key"],
                "difficulty": row.get("difficulty") or "unknown",
                "question": expected.get("question")
                or expected.get("message")
                or row["name"],
                "answer": actual.get("answer") or "",
                "citations": actual.get("citations") or [],
                "retrieved_sections": actual.get("retrieved_sections") or [],
                "gold_sections": expected.get("gold_sections") or [],
            }
        )
    selected = []
    keys = sorted(grouped)
    while len(selected) < limit and any(grouped[key] for key in keys):
        for key in keys:
            if grouped[key] and len(selected) < limit:
                selected.append(grouped[key].pop(0))
    return selected


def save_bias_result(
    *,
    check_type: str,
    judge_model: str,
    sample_count: int,
    metrics: dict[str, Any],
) -> None:
    with _WRITE_LOCK, connection() as conn:
        conn.execute(
            """
            INSERT INTO judge_bias_results(
                check_type, judge_model, sample_count, metric_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (check_type, judge_model, sample_count, _json(metrics), utcnow()),
        )
