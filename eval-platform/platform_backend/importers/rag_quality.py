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
from .rag_ablation import retrieval_metrics


IMPORTER_VERSION = "rag-quality-v1"
DATASET_ID = "imported-rag-quality"


def dry_run(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    return ImportResult(
        source_path=str(path),
        source_hash=source_hash,
        run_id=stable_run_id(source_hash, "rag-quality"),
        run_name="Imported RAG Quality",
        case_count=len(report.get("results") or []),
    )


def import_report(path: Path) -> ImportResult:
    report = json.loads(path.read_bytes())
    source_hash = file_sha256(path)
    run_id = stable_run_id(source_hash, "rag-quality")
    version_hash = stable_version_hash(source_hash, "rag-quality")
    run_name = "Imported RAG Quality"
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

    cases = []
    outcomes = []
    records = []
    for raw in raw_results:
        gold = list(raw.get("gold_sections") or [])
        retrieved = list(raw.get("retrieved_sections") or [])
        metrics = retrieval_metrics(gold, retrieved)
        scores = raw.get("scores") or {}
        metrics.update(
            {
                "judge_scores": scores,
                "recall_at_5": float(raw.get("recall_at_5") or metrics["recall_at_5"]),
                "invalid_citation_count": len(raw.get("invalid_citation_ids") or []),
                "duration_ms": int(raw.get("duration_ms") or 0),
                "retrieval_ms": int(raw.get("retrieval_ms") or 0),
                "generation_ms": int(raw.get("generation_ms") or 0),
                "judge_ms": int(raw.get("judge_ms") or 0),
                "tokens_total": int(
                    (raw.get("generation_usage") or {}).get("total_tokens") or 0
                )
                + int((raw.get("judge_usage") or {}).get("total_tokens") or 0),
                "cost_usd": None,
            }
        )
        passed = bool(raw.get("quality_passed"))
        difficulty = "medium" if "medium" in (raw.get("tags") or []) else "hard"
        case = {
            "case_key": str(raw["id"]),
            "name": str(raw.get("message") or raw["id"]),
            "category": "knowledge_retrieval",
            "severity": "high",
            "difficulty": difficulty,
            "input": {
                "type": "imported_rag_quality",
                "query": raw.get("message") or "",
                "routes": raw.get("routes") or [],
            },
            "expected": {
                "gold_sections": gold,
                "question": raw.get("message") or "",
                "source_report": str(path),
            },
            "tags": ["imported", "rag_quality", "judge"],
        }
        actual = {
            "retrieved_sections": retrieved,
            "answer": raw.get("answer") or "",
            "citations": raw.get("citations") or [],
            "invalid_citation_ids": raw.get("invalid_citation_ids") or [],
            "judge": raw.get("judge") or {},
            "scores": scores,
            "recall_at_5": metrics["recall_at_5"],
            "retrieval_error": raw.get("retrieval_error") or "",
            "error": raw.get("error") or "",
        }
        outcome = {
            "passed": passed,
            "score": (
                sum(float(value) for value in scores.values() if isinstance(value, int | float))
                / len(scores)
                if scores
                else 0.0
            ),
            "duration_ms": int(raw.get("duration_ms") or 0),
            "actual": actual,
            "failures": [] if passed else ["quality_passed_false"],
            "root_causes": [],
            "metrics": metrics,
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
                "category": "knowledge_retrieval",
                "severity": "high",
                "duration_ms": outcome["duration_ms"],
                "root_causes": outcome["root_causes"],
                "attribution": outcome.get("attribution"),
            }
        )

    db.create_imported_dataset(
        dataset_id=DATASET_ID,
        name="RAG quality · Ablation 100",
        description=f"Imported from {path}",
        source_path=str(path),
        version_hash=version_hash,
        rows=cases,
    )
    metrics, root_causes, _ = category_metrics(records)
    report_metrics = report.get("metrics") or {}
    metrics.update(
        {
            "reported_metrics": report_metrics,
            "domain_metrics": report.get("domain_metrics") or {},
            "judge_coverage": report_metrics.get("judge_coverage"),
            "quality_pass_rate": report_metrics.get("quality_pass_rate"),
            "errors": report_metrics.get("errors"),
            "recall_at_15": round(
                sum(item["metrics"]["recall_at_15"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "recall_at_5": round(
                sum(item["metrics"]["recall_at_5"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "mrr_at_5": round(
                sum(item["metrics"]["mrr_at_5"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "ndcg_at_5": round(
                sum(item["metrics"]["ndcg_at_5"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "faithfulness": report_metrics.get("faithfulness"),
            "citation_accuracy": report_metrics.get("citation_accuracy"),
            "answer_relevance": report_metrics.get("answer_relevance"),
            "invalid_citations": sum(
                bool(outcome["actual"].get("invalid_citation_ids"))
                for outcome in outcomes
            ),
        }
    )
    config = imported_system_config(
        config_id="imported-rag-quality",
        name=run_name,
        description=f"RAG quality report: {path}",
        model=str(report.get("answer_model") or "not-reported"),
        rag_mode="hybrid",
    )
    config_record = config.as_record()
    config_record.update(
        {
            "answer_model": report.get("answer_model"),
            "judge_model": report.get("judge_model"),
            "embedding_model": report.get("embedding_model"),
            "cases_sha256": report.get("cases_sha256"),
            "source_report": str(path),
        }
    )
    db.upsert_system_config(config)
    db.create_imported_run(
        run_id=run_id,
        dataset_id=DATASET_ID,
        dataset_version_hash=version_hash,
        name=run_name,
        system_config_id=config.id,
        config={**config_record, "kind": "imported"},
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
