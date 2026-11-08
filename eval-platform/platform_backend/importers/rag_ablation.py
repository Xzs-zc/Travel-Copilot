from __future__ import annotations

import json
import math
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


IMPORTER_VERSION = "rag-ablation-v1"


def _dry_run(path: Path, report: dict[str, Any], mode: str) -> ImportResult:
    return ImportResult(
        source_path=str(path),
        source_hash=file_sha256(path),
        run_id=stable_run_id(file_sha256(path), mode),
        run_name=f"Imported RAG ablation · {mode}",
        case_count=len(((report.get("metrics") or {}).get(mode) or {}).get("cases") or []),
    )


def _run_report_dry_run(
    *,
    path: Path,
    report: dict[str, Any],
    mode: str,
) -> ImportResult:
    return _dry_run(path, report, mode)


def _relevant(gold: list[str], retrieved: list[str], k: int) -> int:
    return len(set(gold) & set(retrieved[:k]))


def retrieval_metrics(gold: list[str], retrieved: list[str]) -> dict[str, float]:
    gold_set = set(gold)
    result = {
        f"recall_at_{k}": (
            len(gold_set & set(retrieved[:k])) / len(gold_set) if gold_set else 0.0
        )
        for k in (1, 3, 5, 15)
    }
    first_rank = next(
        (index for index, item in enumerate(retrieved[:5], start=1) if item in gold_set),
        0,
    )
    result["mrr_at_5"] = 1.0 / first_rank if first_rank else 0.0
    result["hit_at_1"] = 1.0 if _relevant(gold, retrieved, 1) else 0.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, item in enumerate(retrieved[:5], start=1)
        if item in gold_set
    )
    ideal_hits = min(len(gold_set), 5)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    result["ndcg_at_5"] = dcg / idcg if idcg else 0.0
    return {key: round(value, 6) for key, value in result.items()}


def _run_report(
    *,
    path: Path,
    report: dict[str, Any],
    source_hash: str,
    mode: str,
    dataset_id: str,
) -> ImportResult:
    version_hash = stable_version_hash(source_hash, mode)
    run_id = stable_run_id(source_hash, mode)
    run_name = f"Imported RAG ablation · {mode}"
    traces = {item["id"]: item for item in report.get("traces") or []}
    mode_data = (report.get("metrics") or {}).get(mode) or {}
    raw_cases = mode_data.get("cases") or [
        {
            "id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "gold": [
                f"{gold['doc_id']}/{gold['section']}"
                for gold in item.get("gold_sections") or []
            ],
            "retrieved": ((item.get(mode) or {}).get("sections") or []),
        }
        for item in report.get("traces") or []
    ]
    if db.import_exists(source_hash, run_id):
        return ImportResult(
            source_path=str(path),
            source_hash=source_hash,
            run_id=run_id,
            run_name=run_name,
            case_count=len(raw_cases),
            skipped=True,
        )

    cases: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for raw in raw_cases:
        case_key = str(raw["id"])
        source_trace = traces.get(case_key) or {}
        gold = list(raw.get("gold") or [])
        retrieved = list(raw.get("retrieved") or [])
        scores = retrieval_metrics(gold, retrieved)
        passed = scores["recall_at_5"] == 1.0
        severity = "high"
        input_data = {
            "type": "imported_rag_retrieval",
            "query": source_trace.get("message", ""),
            "routes": source_trace.get("routes", []),
            "mode": mode,
        }
        expected = {
            "gold_sections": gold,
            "source_report": str(path),
        }
        actual = {
            "retrieved_sections": retrieved,
            "scores": scores,
            "retrieval_error": (source_trace.get(mode) or {}).get("error", ""),
        }
        case = {
            "case_key": case_key,
            "name": source_trace.get("message") or case_key,
            "category": "knowledge_retrieval",
            "severity": severity,
            "difficulty": raw.get("difficulty", "medium"),
            "input": input_data,
            "expected": expected,
            "tags": ["imported", "rag_ablation", mode],
        }
        cases.append(case)
        outcome = {
            "passed": passed,
            "score": scores["recall_at_5"] * 100,
            "duration_ms": 0,
            "actual": actual,
            "failures": [] if passed else ["recall_at_5_below_1"],
            "root_causes": [],
            "metrics": scores,
            "trace_id": report.get("run_id", ""),
        }
        if not passed:
            attribution = attribute_failure(case, actual)
            outcome["attribution"] = attribution.model_dump(mode="json")
            outcome["root_causes"] = [
                attribution.primary,
                *attribution.secondary,
            ]
        outcomes.append(outcome)
        records.append(
            {
                "status": "passed" if passed else "failed",
                "category": "knowledge_retrieval",
                "severity": severity,
                "duration_ms": 0,
                "root_causes": outcome["root_causes"],
                "attribution": outcome.get("attribution"),
            }
        )

    db.create_imported_dataset(
        dataset_id=dataset_id,
        name=f"RAG ablation {mode}",
        description=f"Imported from {path}",
        source_path=str(path),
        version_hash=version_hash,
        rows=cases,
    )
    metrics, root_causes, _ = category_metrics(records)
    metrics.update(
        {
            "retrieval_mode": mode,
            "recall_at_1": round(
                sum(item["metrics"]["recall_at_1"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "recall_at_3": round(
                sum(item["metrics"]["recall_at_3"] for item in outcomes)
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
            "hit_at_1": round(
                sum(item["metrics"]["hit_at_1"] for item in outcomes)
                / len(outcomes)
                * 100,
                1,
            ),
            "reported_recall_at_5": mode_data.get("overall", {}).get("recall_at_5"),
            "reported_recall_at_15": mode_data.get("overall", {}).get("recall_at_15"),
        }
    )
    model = str(report.get("embedding_model") or "not-reported")
    config = imported_system_config(
        config_id=dataset_id,
        name=run_name,
        description=f"Historical RAG ablation report: {path}",
        model=model,
        rag_mode="hybrid" if mode.startswith("hybrid") else "lexical",
    )
    config_record = config.as_record()
    config_record.update(
        {
            "answer_model": report.get("answer_model"),
            "judge_model": report.get("judge_model"),
            "embedding_model": report.get("embedding_model"),
            "source_report": str(path),
        }
    )
    db.upsert_system_config(config)
    db.create_imported_run(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_version_hash=version_hash,
        name=run_name,
        system_config_id=config.id,
        config={**config_record, "kind": "imported"},
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
                source_trace=(traces.get(case["case_key"]) or {}).get(mode, {}).get("events", []),
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


def import_report(
    path: Path,
    *,
    modes: tuple[str, ...] = ("vector", "hybrid", "hybrid_rerank"),
    dataset_id_prefix: str = "imported-rag-ablation",
) -> list[ImportResult]:
    kb = path.read_bytes()
    report = json.loads(kb)
    source_hash = file_sha256(path)
    return [
        _run_report(
            path=path,
            report=report,
            source_hash=source_hash,
            mode=mode,
            dataset_id=f"{dataset_id_prefix}-{mode.replace('_', '-')}",
        )
        for mode in modes
    ]
