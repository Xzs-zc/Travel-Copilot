from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..core.attribution import attribute_failure
from .common import (
    ImportResult,
    category_metrics,
    file_sha256,
    imported_system_config,
    imported_trace,
    stable_run_id,
    stable_version_hash,
)


IMPORTER_VERSION = "support-blind-v1"
BLOCK_CATEGORY = {
    "router": "support_routing",
    "rag": "knowledge_retrieval",
    "fault": "reliability",
    "safety": "safety_policy",
    "e2e": "e2e",
}


def _dry_run(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    count = sum(
        len(report.get(block) or [])
        for block in (
            "router_results",
            "rag_results",
            "fault_results",
            "safety_results",
            "e2e_results",
        )
    )
    return ImportResult(
        source_path=str(path),
        source_hash=source_hash,
        run_id=stable_run_id(source_hash, "support-blind"),
        run_name="Imported Support Blind v5",
        case_count=count,
    )


def import_report(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    run_id = stable_run_id(source_hash, "support-blind")
    version_hash = stable_version_hash(source_hash, "support-blind")
    dataset_id = "imported-support-blind-v5"
    run_name = "Imported Support Blind v5"
    blocks = ("router_results", "rag_results", "fault_results", "safety_results", "e2e_results")
    raw_results = [
        item
        for block in blocks
        for item in (report.get(block) or [])
    ]
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
        block = str(raw.get("block") or "")
        category = BLOCK_CATEGORY.get(block, block or "support")
        mode = str(raw.get("mode") or "")
        case_key = f"{block}:{mode}:{raw['id']}"
        passed = bool(raw.get("passed"))
        expected = raw.get("expected") or {}
        severity = "critical" if category == "safety_policy" or expected.get("critical_route") else "high"
        question = str(expected.get("question") or raw.get("name") or case_key)
        case = {
            "case_key": case_key,
            "name": question,
            "category": category,
            "severity": severity,
            "difficulty": "medium",
            "input": {
                "type": "imported_support_blind",
                "block": block,
                "mode": mode,
                "question": question,
            },
            "expected": expected,
            "tags": ["imported", "support_blind_v5", block],
        }
        outcome = {
            "passed": passed,
            "score": 100.0 if passed else 0.0,
            "duration_ms": int(raw.get("duration_ms") or 0),
            "actual": raw.get("actual") or {},
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
        name="Support blind v5 historical report",
        description=f"Imported from {path}",
        source_path=str(path),
        version_hash=version_hash,
        rows=cases,
    )
    metrics, root_causes, categories = category_metrics(records)
    metrics.update(
        {
            "reported_metrics": report.get("metrics") or {},
            "reported_categories": categories,
        }
    )
    config = imported_system_config(
        config_id="imported-support-blind-v5",
        name=run_name,
        description=f"Historical support blind report: {path}",
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
        duration_ms=0,
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
                source_trace=list(outcome["actual"].get("trace") or []),
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
