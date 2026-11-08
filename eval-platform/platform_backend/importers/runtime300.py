from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..core.attribution import attribute_failure
from .common import (
    ImportResult,
    category_metrics,
    duration_ms,
    file_sha256,
    imported_system_config,
    imported_trace,
    stable_run_id,
    stable_version_hash,
)


IMPORTER_VERSION = "runtime300-v1"


def _dry_run(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    return ImportResult(
        source_path=str(path),
        source_hash=source_hash,
        run_id=stable_run_id(source_hash, "runtime300"),
        run_name="Imported Runtime300",
        case_count=len(report.get("results") or []),
    )


def import_report(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    run_id = stable_run_id(source_hash, "runtime300")
    version_hash = stable_version_hash(source_hash, "runtime300")
    dataset_id = "imported-runtime300"
    run_name = "Imported Runtime300"
    raw_results = report.get("results") or []
    if db.import_exists(source_hash, run_id):
        return ImportResult(
            source_path=str(path),
            source_hash=source_hash,
            run_id=run_id,
            run_name=run_name,
            case_count=len(raw_results),
            skipped=True,
        )

    cases: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for raw in raw_results:
        case_key = str(raw["id"])
        category = str(raw.get("category") or "runtime")
        passed = bool(raw.get("passed"))
        severity = "critical" if category in {"safety", "reliability"} else "high"
        case = {
            "case_key": case_key,
            "name": str(raw.get("name") or case_key),
            "category": category,
            "severity": severity,
            "difficulty": "medium",
            "input": {
                "type": "imported_runtime300",
                "mode": raw.get("mode", ""),
                "metrics": raw.get("metrics") or {},
            },
            "expected": {"passed": True, "source_result": case_key},
            "tags": ["imported", "runtime300", category],
        }
        outcome = {
            "passed": passed,
            "score": float(raw.get("score") or (100 if passed else 0)),
            "duration_ms": int(raw.get("duration_ms") or 0),
            "actual": {
                "metrics": raw.get("metrics") or {},
                "trace": raw.get("trace") or [],
            },
            "failures": list(raw.get("failures") or []),
            "root_causes": list(raw.get("root_causes") or []),
            "metrics": raw.get("metrics") or {},
            "trace_id": str(report.get("run_id") or ""),
        }
        if not passed:
            attribution = attribute_failure(
                case,
                outcome["actual"],
                source_root_causes=outcome["root_causes"],
            )
            outcome["attribution"] = attribution.model_dump(mode="json")
            outcome["root_causes"] = [
                attribution.primary,
                *attribution.secondary,
            ]
        cases.append(case)
        outcomes.append(outcome)
        records.append(
            {
                "status": "passed" if passed else "failed",
                "category": category,
                "severity": severity,
                "duration_ms": outcome["duration_ms"],
                "root_causes": outcome["root_causes"],
                "attribution": outcome.get("attribution"),
            }
        )

    db.create_imported_dataset(
        dataset_id=dataset_id,
        name="Runtime300 historical report",
        description=f"Imported from {path}",
        source_path=str(path),
        version_hash=version_hash,
        rows=cases,
    )
    metrics, root_causes, categories = category_metrics(records)
    report_metrics = report.get("metrics") or {}
    metrics.update(
        {
            "reported_task_success_rate": report_metrics.get("task_success_rate"),
            "reported_p95_ms": report_metrics.get("p95_ms"),
            "reported_online_e2e_cases": report_metrics.get("online_e2e_cases"),
            "reported_categories": report_metrics.get("categories") or categories,
        }
    )
    config = imported_system_config(
        config_id="imported-runtime300",
        name=run_name,
        description=f"Historical Runtime300 report: {path}",
        model="not-reported",
        rag_mode="hybrid",
    )
    db.upsert_system_config(config)
    db.create_imported_run(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_version_hash=version_hash,
        name=run_name,
        system_config_id=config.id,
        config={
            **config.as_record(),
            "kind": "imported",
            "source_report": str(path),
            "source_run_id": report.get("run_id"),
        },
        metrics=metrics,
        root_causes=root_causes,
        started_at=str(report.get("started_at") or ""),
        finished_at=str(report.get("finished_at") or ""),
        duration_ms=duration_ms(
            str(report.get("started_at") or ""),
            str(report.get("finished_at") or ""),
        ),
    )
    for case, outcome in zip(cases, outcomes, strict=True):
        db.insert_case_result(run_id, case, outcome)
        db.insert_trace_spans(
            run_id,
            case["case_key"],
            imported_trace(
                source_run_id=str(report.get("run_id") or run_id),
                case_key=case["case_key"],
                passed=outcome["passed"],
                duration_ms=outcome["duration_ms"],
                input_data=case["input"],
                actual=outcome["actual"],
                source_trace=outcome["actual"]["trace"],
            ),
        )
    db.record_import(
        source_hash=source_hash,
        run_id=run_id,
        source_path=str(path),
        importer_version=IMPORTER_VERSION,
    )
    return ImportResult(
        source_path=str(path),
        source_hash=source_hash,
        run_id=run_id,
        run_name=run_name,
        case_count=len(cases),
    )
