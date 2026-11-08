from __future__ import annotations

import json
from collections import defaultdict
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


IMPORTER_VERSION = "online-support-e2e-v2"
DATASET_ID = "imported-online-support-e2e"


def dry_run(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    return ImportResult(
        source_path=str(path),
        source_hash=source_hash,
        run_id=stable_run_id(source_hash, "online-support-e2e"),
        run_name="Imported Online Support E2E",
        case_count=int(report.get("case_count") or 0),
    )


def import_report(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    run_id = stable_run_id(source_hash, "online-support-e2e")
    version_hash = stable_version_hash(source_hash, "online-support-e2e")
    run_name = "Imported Online Support E2E"
    if db.import_exists(source_hash, run_id, IMPORTER_VERSION):
        return ImportResult(
            source_path=str(path),
            source_hash=source_hash,
            run_id=run_id,
            run_name=run_name,
            case_count=int(report.get("case_count") or 0),
            skipped=True,
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in report.get("results") or []:
        grouped[result["case_key"]].append(result)

    cases = []
    outcomes = []
    records = []
    for case_key, trials in sorted(grouped.items()):
        valid = [trial for trial in trials if trial["status"] != "error"]
        passed = len(valid) == int(report["trials"]) and all(
            trial["status"] == "passed" for trial in valid
        )
        failures = [
            f"trial_{trial['trial']}:{failure}"
            for trial in trials
            for failure in trial.get("failures") or []
        ]
        expected = trials[0].get("expected") or {}
        actual = {
            "trials": [
                {
                    "trial": trial["trial"],
                    "status": trial["status"],
                    "duration_ms": trial["duration_ms"],
                    "actual": trial.get("actual") or {},
                    "failures": trial.get("failures") or [],
                }
                for trial in trials
            ],
            "timing_scope": "consumer_arrival",
        }
        case = {
            "case_key": case_key,
            "name": f"Online support {case_key}",
            "category": "online_e2e",
            "severity": "high",
            "difficulty": "medium",
            "input": {
                "type": "support_e2e",
                "message": expected.get("question") or "",
                "expected": expected,
            },
            "expected": expected,
            "tags": ["imported", "online", "support_e2e"],
        }
        token_total = sum(
            int((trial.get("token_usage") or {}).get("total_tokens") or 0)
            for trial in trials
        )
        outcome = {
            "passed": passed,
            "score": (
                sum(trial["status"] == "passed" for trial in trials)
                / len(trials)
                * 100
            ) if trials else 0,
            "duration_ms": int(
                sum(int(trial["duration_ms"]) for trial in trials) / len(trials)
            ) if trials else 0,
            "actual": actual,
            "failures": failures,
            "root_causes": [],
            "metrics": {
                "trials": len(trials),
                "passed_trials": sum(trial["status"] == "passed" for trial in trials),
                "tokens_total": token_total,
                "timing_scope": "consumer_arrival",
            },
            "trace_id": str(report.get("run_id") or ""),
        }
        if not passed:
            attribution = attribute_failure(case, actual)
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
                "category": "online_e2e",
                "severity": "high",
                "duration_ms": outcome["duration_ms"],
                "root_causes": outcome["root_causes"],
                "attribution": outcome.get("attribution"),
            }
        )

    db.create_imported_dataset(
        dataset_id=DATASET_ID,
        name="Online Support E2E",
        description=f"Imported from {path}",
        source_path=str(path),
        version_hash=version_hash,
        rows=cases,
    )
    metrics, root_causes, _ = category_metrics(records)
    metrics.update(
        {
            "trials": report.get("trials"),
            "executions": report.get("executions"),
            "valid_executions": report.get("valid_executions"),
            "error_rate": report.get("error_rate"),
            "pass_at_3": report.get("pass_at_3"),
            "pass_hat_3": report.get("pass_hat_3"),
            "pass_at_k": (
                float(report["pass_at_3"]) / 100
                if report.get("pass_at_3") is not None else None
            ),
            "pass_hat_k": (
                float(report["pass_hat_3"]) / 100
                if report.get("pass_hat_3") is not None else None
            ),
            "p50_duration_ms": report.get("p50_ms"),
            "p95_duration_ms": report.get("p95_ms"),
            "timing_scope": report.get("timing_scope"),
            "tokens_total": report.get("token_usage", {}).get("total_tokens"),
            "cost_usd": None,
            "cost_amount": report.get("cost", {}).get("total_rmb"),
            "cost_currency": report.get("cost", {}).get("currency"),
            "flaky_cases": report.get("flaky_cases") or [],
            "flaky_cases_count": len(report.get("flaky_cases") or []),
        }
    )
    config = imported_system_config(
        config_id="imported-online-support-e2e",
        name=run_name,
        description=f"Online support E2E report: {path}",
        model="deepseek-flash",
        rag_mode="hybrid",
    )
    db.upsert_system_config(config)
    db.create_imported_run(
        run_id=run_id,
        dataset_id=DATASET_ID,
        dataset_version_hash=version_hash,
        name=run_name,
        system_config_id=config.id,
        config={
            **config.as_record(),
            "kind": "imported",
            "source_report": str(path),
            "timing_scope": "consumer_arrival",
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
        for trial in outcome["actual"]["trials"]:
            db.insert_trial_result(
                run_id,
                case["case_key"],
                int(trial["trial"]),
                {
                    "passed": trial["status"] == "passed",
                    "execution_status": trial["status"],
                    "score": 100.0 if trial["status"] == "passed" else 0.0,
                    "duration_ms": int(trial["duration_ms"]),
                    "actual": trial.get("actual") or {},
                    "failures": trial.get("failures") or [],
                    "metrics": {
                        "status": trial["status"],
                        "timing_scope": "consumer_arrival",
                    },
                },
            )
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
                source_trace=[],
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
