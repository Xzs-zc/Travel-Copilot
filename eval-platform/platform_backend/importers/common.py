from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.system_config import SystemConfig
from ..core.tracing import SpanRecord


@dataclass
class ImportResult:
    source_path: str
    source_hash: str
    run_id: str
    run_name: str
    case_count: int
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "run_id": self.run_id,
            "run_name": self.run_name,
            "case_count": self.case_count,
            "skipped": self.skipped,
        }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_run_id(source_hash: str, suffix: str = "") -> str:
    value = f"{source_hash}:{suffix}".encode("utf-8")
    return f"imp-{hashlib.sha256(value).hexdigest()[:16]}"


def stable_version_hash(source_hash: str, suffix: str = "") -> str:
    return hashlib.sha256(f"{source_hash}:{suffix}".encode("utf-8")).hexdigest()


def imported_system_config(
    *,
    config_id: str,
    name: str,
    description: str,
    model: str,
    rag_mode: str,
) -> SystemConfig:
    normalized_rag_mode = rag_mode if rag_mode in {"off", "lexical", "hybrid"} else "hybrid"
    return SystemConfig(
        id=config_id,
        name=name,
        description=description,
        rag_mode=normalized_rag_mode,
        policy_gate=True,
        validators="strict",
        prompt_version="imported-report",
        model=model or "not-reported",
    )


def duration_ms(started_at: str, finished_at: str) -> int:
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
        return max(0, int((finished - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0


def category_metrics(
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    total = len(records)
    passed = sum(1 for item in records if item["status"] == "passed")
    by_category: dict[str, dict[str, Any]] = {}
    for item in records:
        row = by_category.setdefault(
            item["category"],
            {"total": 0, "passed": 0, "duration_ms": []},
        )
        row["total"] += 1
        row["passed"] += int(item["status"] == "passed")
        row["duration_ms"].append(item["duration_ms"])
    categories = {
        name: {
            "total": value["total"],
            "passed": value["passed"],
            "pass_rate": round(value["passed"] / value["total"] * 100, 1),
            "avg_duration_ms": round(
                sum(value["duration_ms"]) / len(value["duration_ms"]),
                1,
            ),
        }
        for name, value in by_category.items()
    }
    durations = sorted(item["duration_ms"] for item in records)
    p95 = (
        durations[min(len(durations) - 1, math.ceil(len(durations) * 0.95) - 1)]
        if durations
        else 0
    )
    root_causes: dict[str, int] = {}
    for item in records:
        for cause in item.get("root_causes", []):
            root_causes[cause] = root_causes.get(cause, 0) + 1
    failed_records = [item for item in records if item["status"] == "failed"]
    covered = sum(
        (item.get("attribution") or {}).get("method")
        not in {None, "category_fallback"}
        for item in failed_records
    )
    unclassified = sum(
        (item.get("attribution") or {}).get("primary") == "unclassified"
        for item in failed_records
    )
    metrics = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0,
        "critical_failures": sum(
            1
            for item in records
            if item["status"] == "failed" and item["severity"] == "critical"
        ),
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0,
        "p95_duration_ms": p95,
        "avg_tokens": 0,
        "p95_tokens": 0,
        "avg_cost_usd": 0,
        "p95_cost_usd": 0,
        "total_cost_usd": 0,
        "attribution_coverage": (
            round(covered / len(failed_records), 4)
            if failed_records
            else 1.0
        ),
        "unclassified_rate": (
            round(unclassified / len(failed_records), 4)
            if failed_records
            else 0.0
        ),
        "categories": categories,
    }
    return metrics, root_causes, categories


def imported_trace(
    *,
    source_run_id: str,
    case_key: str,
    passed: bool,
    duration_ms: int,
    input_data: Any,
    actual: Any,
    source_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    root_id = hashlib.sha256(f"{source_run_id}:{case_key}".encode()).hexdigest()[:16]
    root = SpanRecord(
        trace_id=source_run_id,
        span_id=root_id,
        parent_span_id=None,
        name=case_key,
        span_type="imported",
        status="passed" if passed else "failed",
        start_ms=0,
        duration_ms=float(duration_ms),
        input=input_data,
        output=actual,
        metadata={"source": "offline_import", "source_events": len(source_trace)},
    ).as_dict()
    spans = [root]
    timed = [
        event
        for event in source_trace
        if event.get("start_ms") is not None and event.get("duration_ms") is not None
    ]
    if timed:
        for index, event in enumerate(timed):
            spans.append(
                SpanRecord(
                    trace_id=source_run_id,
                    span_id=str(event.get("span_id") or f"{root_id}-{index}"),
                    parent_span_id=root_id,
                    name=str(event.get("event") or event.get("name") or f"event-{index}"),
                    span_type=str(event.get("type") or "event"),
                    status=str(event.get("status") or "passed"),
                    start_ms=float(event["start_ms"]),
                    duration_ms=float(event["duration_ms"]),
                    input=event.get("input"),
                    output=event.get("data"),
                    metadata=event.get("meta") or {},
                    error_text=str(event.get("error_text") or ""),
                    tokens_in=event.get("tokens_in"),
                    tokens_out=event.get("tokens_out"),
                    cost_usd=event.get("cost_usd"),
                    model=event.get("model"),
                ).as_dict()
            )
    elif source_trace:
        spans.append(
            SpanRecord(
                trace_id=source_run_id,
                span_id=f"{root_id}-unavailable",
                parent_span_id=root_id,
                name="source.trace",
                span_type="imported",
                status="unavailable",
                start_ms=0,
                duration_ms=0,
                output={"event_count": len(source_trace)},
                error_text="source_events_missing_timing",
            ).as_dict()
        )
    return spans
