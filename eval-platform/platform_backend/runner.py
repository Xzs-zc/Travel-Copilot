from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .adapters.observability import (
    finish_trace,
    flush,
    observation,
    score_trace,
    start_trace,
)
from .config import LANGFUSE_EXPORT_ENABLED, PLATFORM_ROOT
from .core.attribution import Attribution
from .core.failure_taxonomy import FailureCause
from .core.system_config import SystemConfig
from .core.stats import pass_at_k, pass_hat_k
from .core.task import TaskOutput, execute_task
from .core.tracing import span, trace_session
from .db import (
    fail_run,
    finish_run,
    get_system_config,
    insert_case_result,
    insert_trace_spans,
    insert_trial_result,
    list_case_results,
    update_run_status,
)
from .graders import GradeResult, grade_case


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return int(ordered[index])


def _p95_float(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 6)


@lru_cache(maxsize=1)
def _deepseek_pricing() -> dict[str, float] | None:
    path = Path(PLATFORM_ROOT) / "configs" / "pricing.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pricing = payload.get("deepseek") or {}
        return {
            "input_cache_hit": float(pricing["input_cache_hit_per_million"]),
            "input_cache_miss": float(pricing["input_cache_miss_per_million"]),
            "output": float(pricing["output_per_million"]),
        }
    except (FileNotFoundError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def _deepseek_cost_amount(token_usage: dict[str, Any]) -> float | None:
    pricing = _deepseek_pricing()
    if not token_usage or pricing is None:
        return None
    prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
    completion_tokens = int(token_usage.get("completion_tokens") or 0)
    cache_hit_tokens = int(token_usage.get("prompt_cache_hit_tokens") or 0)
    cache_miss_tokens = int(
        token_usage.get("prompt_cache_miss_tokens")
        or max(0, prompt_tokens - cache_hit_tokens)
    )
    return round(
        (
            cache_hit_tokens * pricing["input_cache_hit"]
            + cache_miss_tokens * pricing["input_cache_miss"]
            + completion_tokens * pricing["output"]
        )
        / 1_000_000,
        6,
    )


async def evaluate_case(
    case: dict[str, Any],
    *,
    config: SystemConfig,
    run_id: str,
    trial: int = 0,
) -> dict[str, Any]:
    langfuse_handle = None
    if LANGFUSE_EXPORT_ENABLED:
        langfuse_handle = start_trace(
            name=f"eval:{case['case_key']}",
            input=case["input"],
            metadata={
                "run_id": run_id,
                "category": case["category"],
                "system_config_id": config.id,
                "trial": trial,
            },
            session_id=run_id,
            tags=["traveling", "business-eval", case["category"]],
        )
    with trace_session(case["case_key"]) as tracer:
        with span(
            case["case_key"],
            "agent",
            input=case["input"],
            metadata={
                "category": case["category"],
                "severity": case["severity"],
                "system_config_id": config.id,
                "trial": trial,
            },
        ) as root_span:
            try:
                if LANGFUSE_EXPORT_ENABLED:
                    with observation(
                        name=f"langfuse:{case['case_key']}",
                        as_type="span",
                        input=case["input"],
                        metadata={
                            "expected": case["expected"],
                            "system_config_id": config.id,
                        },
                        trace_context=langfuse_handle.context if langfuse_handle else None,
                    ) as langfuse_span:
                        task_output = await execute_task(case, config)
                        grade = grade_case(case, task_output.actual)
                        if langfuse_span is not None:
                            langfuse_span.update(
                                output={
                                    "passed": grade.passed,
                                    "actual": task_output.actual,
                                    "failures": grade.failures,
                                }
                            )
                        if langfuse_handle:
                            score_trace(
                                langfuse_handle,
                                "case_pass",
                                1 if grade.passed else 0,
                                data_type="BOOLEAN",
                                comment="; ".join(grade.failures),
                            )
                else:
                    task_output = await execute_task(case, config)
                    grade = grade_case(case, task_output.actual)
            except Exception as exc:
                task_output = TaskOutput(actual={"error": str(exc)})
                grade = GradeResult(
                    passed=False,
                    failures=[f"runner_exception:{type(exc).__name__}"],
                    root_causes=[FailureCause.ENVIRONMENT_ERROR.value],
                    attribution=Attribution(
                        primary=FailureCause.ENVIRONMENT_ERROR.value,
                        evidence={"exception": str(exc)[:1000]},
                        method="rule",
                        rule_id="runner.exception",
                    ),
                )
            root_span.update(
                output={
                    "passed": grade.passed,
                    "actual": task_output.actual,
                    "failures": grade.failures,
                    "root_causes": grade.root_causes,
                },
                status="passed" if grade.passed else "failed",
            )
        spans = tracer.records()
        trace_id = tracer.trace_id
        duration_ms = int(root_span.record.duration_ms) if root_span.record else 0
    if langfuse_handle:
        finish_trace(
            langfuse_handle,
            output={"passed": grade.passed, "failures": grade.failures},
            metadata={"root_causes": grade.root_causes},
        )
    insert_trace_spans(run_id, case["case_key"], spans)
    token_usage = task_output.actual.get("token_usage") or {}
    cost_amount = _deepseek_cost_amount(token_usage)
    execution_status = (
        "error"
        if task_output.actual.get("status") == "error"
        else "passed" if grade.passed else "failed"
    )
    return {
        "passed": grade.passed,
        "execution_status": execution_status,
        "score": 100.0 if grade.passed else 0.0,
        "duration_ms": duration_ms,
        "actual": task_output.actual,
        "failures": grade.failures,
        "root_causes": grade.root_causes,
        "attribution": (
            grade.attribution.model_dump(mode="json")
            if grade.attribution
            else None
        ),
        "metrics": {
            "category": case["category"],
            "severity": case["severity"],
            "system_config_id": config.id,
            "trial": trial,
            "execution_status": execution_status,
            "tokens_total": (
                int(token_usage.get("total_tokens") or 0)
                if token_usage
                else None
            ),
            "cost_amount": cost_amount,
            "cost_currency": "CNY" if cost_amount is not None else "",
            "langfuse_trace_id": langfuse_handle.trace_id if langfuse_handle else "",
        },
        "trace_id": trace_id,
    }


def _metrics(results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    durations = [int(item["duration_ms"]) for item in results]
    tokens = [int(item.get("tokens_total") or 0) for item in results]
    costs = [float(item.get("cost_usd") or 0.0) for item in results]
    costs_rmb = [
        float(item.get("cost_amount") or 0.0)
        for item in results
    ]
    passed = sum(1 for item in results if item["status"] == "passed")
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        category_rows[item["category"]].append(item)
    categories = {}
    for category, rows in category_rows.items():
        category_passed = sum(1 for item in rows if item["status"] == "passed")
        categories[category] = {
            "total": len(rows),
            "passed": category_passed,
            "pass_rate": round(category_passed / len(rows) * 100, 1) if rows else 0,
            "avg_duration_ms": round(
                sum(item["duration_ms"] for item in rows) / len(rows),
                1,
            ),
        }
    root_causes = Counter(
        cause for item in results for cause in item.get("root_causes", [])
    )
    failed_results = [item for item in results if item["status"] == "failed"]
    covered = sum(
        (item.get("attribution") or {}).get("method")
        not in {None, "category_fallback"}
        for item in failed_results
    )
    unclassified = sum(
        (item.get("attribution") or {}).get("primary") == "unclassified"
        for item in failed_results
    )
    metrics = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results) * 100, 1) if results else 0,
        "critical_failures": sum(
            1
            for item in results
            if item["status"] == "failed" and item["severity"] == "critical"
        ),
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0,
        "p95_duration_ms": _p95(durations),
        "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "p95_tokens": _p95_float([float(value) for value in tokens]),
        "avg_cost_usd": round(sum(costs) / len(costs), 6) if costs else 0,
        "p95_cost_usd": _p95_float(costs),
        "total_cost_usd": round(sum(costs), 6),
        "total_cost_amount": round(sum(costs_rmb), 6),
        "cost_currency": "CNY" if sum(costs_rmb) else "",
        "attribution_coverage": (
            round(covered / len(failed_results), 4)
            if failed_results
            else 1.0
        ),
        "unclassified_rate": (
            round(unclassified / len(failed_results), 4)
            if failed_results
            else 0.0
        ),
        "categories": categories,
    }
    return metrics, dict(root_causes)


def _aggregate_trials(
    case: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    trial_count = len(outcomes)
    valid_outcomes = [
        item for item in outcomes
        if item.get("execution_status") != "error"
    ]
    valid_count = len(valid_outcomes)
    passed_count = sum(bool(item["passed"]) for item in valid_outcomes)
    error_count = trial_count - valid_count
    root_causes = Counter(
        cause for item in valid_outcomes for cause in item.get("root_causes", [])
    )
    failures = [
        f"trial_{index + 1}:{failure}"
        for index, item in enumerate(outcomes)
        for failure in item.get("failures", [])
    ]
    metrics = dict(outcomes[0]["metrics"])
    metrics.update(
        {
            "trials": trial_count,
            "passed_trials": passed_count,
            "valid_trials": valid_count,
            "errors": error_count,
            "error_rate": round(error_count / trial_count, 6) if trial_count else 0.0,
            "observed_pass_rate": (
                round(passed_count / valid_count, 6) if valid_count else 0.0
            ),
            "pass_at_k": (
                pass_at_k(valid_count, passed_count, trial_count)
                if valid_count else 0.0
            ),
            "pass_hat_k": (
                pass_hat_k(valid_count, passed_count, trial_count)
                if valid_count else 0.0
            ),
            "tokens_total": sum(
                int(item.get("metrics", {}).get("tokens_total") or 0)
                for item in outcomes
            ) if any(
                item.get("metrics", {}).get("tokens_total") is not None
                for item in outcomes
            ) else None,
            "cost_usd": sum(
                float(item.get("metrics", {}).get("cost_usd") or 0)
                for item in outcomes
            ) if any(
                item.get("metrics", {}).get("cost_usd") is not None
                for item in outcomes
            ) else None,
            "cost_amount": sum(
                float(item.get("metrics", {}).get("cost_amount") or 0)
                for item in outcomes
            ) if any(
                item.get("metrics", {}).get("cost_amount") is not None
                for item in outcomes
            ) else None,
            "cost_currency": (
                "CNY"
                if any(
                    item.get("metrics", {}).get("cost_amount") is not None
                    for item in outcomes
                )
                else ""
            ),
            "tool_calls": sum(
                int(item.get("metrics", {}).get("tool_calls") or 0)
                for item in outcomes
            ) or None,
            "steps": sum(
                int(item.get("metrics", {}).get("steps") or 0)
                for item in outcomes
            ) or None,
        }
    )
    actual_fingerprints = {
        json.dumps(
            item["actual"],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        for item in outcomes
    }
    return {
        "passed": valid_count > 0 and passed_count == valid_count and not error_count,
        "score": (
            sum(float(item["score"]) for item in valid_outcomes) / valid_count
            if valid_count else 0.0
        ),
        "duration_ms": int(
            sum(int(item["duration_ms"]) for item in outcomes) / trial_count
        ),
        "actual": (
            outcomes[0]["actual"]
            if len(actual_fingerprints) == 1
            else {"trials": [item["actual"] for item in outcomes]}
        ),
        "failures": failures,
        "root_causes": list(root_causes),
        "attribution": next(
            (
                item.get("attribution")
                for item in valid_outcomes
                if item.get("attribution")
            ),
            None,
        ),
        "metrics": metrics,
        "trace_id": outcomes[0]["trace_id"],
    }


def _online_execution_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    if not trials:
        return {}
    executions = len(trials)
    errors = sum(item["status"] == "error" for item in trials)
    valid = [item for item in trials if item["status"] != "error"]
    valid_executions = len(valid)
    passed = sum(item["status"] == "passed" for item in valid)
    durations = [int(item["duration_ms"]) for item in trials]
    token_values = [
        item.get("metrics", {}).get("tokens_total")
        for item in trials
        if item.get("metrics", {}).get("tokens_total") is not None
    ]
    cost_values = [
        item.get("metrics", {}).get("cost_amount")
        for item in trials
        if item.get("metrics", {}).get("cost_amount") is not None
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in trials:
        grouped[item["case_key"]].append(item)

    pass_at_values = []
    pass_hat_values = []
    flaky_cases = []
    for case_key, case_trials in sorted(grouped.items()):
        case_valid = [item for item in case_trials if item["status"] != "error"]
        if not case_valid:
            continue
        case_passed = sum(item["status"] == "passed" for item in case_valid)
        pass_at_values.append(pass_at_k(len(case_valid), case_passed, len(case_trials)))
        pass_hat_values.append(
            pass_hat_k(len(case_valid), case_passed, len(case_trials))
        )
        valid_statuses = {item["status"] for item in case_valid}
        if len(valid_statuses) > 1:
            flaky_cases.append(
                {
                    "case_key": case_key,
                    "statuses": [item["status"] for item in case_trials],
                    "passed_trials": case_passed,
                    "valid_trials": len(case_valid),
                }
            )

    pass_at_average = (
        round(sum(pass_at_values) / len(pass_at_values), 6)
        if pass_at_values else None
    )
    pass_hat_average = (
        round(sum(pass_hat_values) / len(pass_hat_values), 6)
        if pass_hat_values else None
    )
    return {
        "executions": executions,
        "valid_executions": valid_executions,
        "errors": errors,
        "error_rate": round(errors / executions, 6),
        "pass_rate": round(passed / valid_executions * 100, 6)
        if valid_executions else 0.0,
        "pass_at_k": pass_at_average,
        "pass_hat_k": pass_hat_average,
        "pass_at_3": (
            round(pass_at_average * 100, 1)
            if pass_at_average is not None else None
        ),
        "pass_hat_3": (
            round(pass_hat_average * 100, 1)
            if pass_hat_average is not None else None
        ),
        "p50_duration_ms": _p95(durations) if len(durations) == 1 else (
            sorted(durations)[max(0, math.ceil(len(durations) * 0.5) - 1)]
        ),
        "p95_duration_ms": _p95(durations),
        "timing_scope": "consumer_arrival",
        "tokens_total": sum(int(value) for value in token_values)
        if token_values else None,
        "cost_amount": round(sum(float(value) for value in cost_values), 6)
        if cost_values else None,
        "cost_currency": "CNY" if cost_values else "",
        "flaky_cases": flaky_cases,
        "flaky_cases_count": len(flaky_cases),
    }


async def run_dataset(
    *,
    run_id: str,
    cases: list[dict[str, Any]],
    variant: str,
    trials: int = 1,
    seed: int = 42,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = get_system_config(variant)
    if config is None:
        raise ValueError(f"Unknown system config: {variant}")
    update_run_status(run_id, "running")
    try:
        deterministic = all(
            case["input"]["type"]
            in {
                "transport_recommendation",
                "argument_validation",
                "tool_result",
                "final_validation",
                "support_route",
                "knowledge_retrieval",
                "safety_tool",
                "date_delta",
                "trace_audit",
            }
            for case in cases
        )
        for case in cases:
            trial_outcomes = []
            for trial in range(1, trials + 1):
                outcome = await evaluate_case(
                    case,
                    config=config,
                    run_id=run_id,
                    trial=trial,
                )
                insert_trial_result(
                    run_id,
                    case["case_key"],
                    trial,
                    outcome,
                )
                trial_outcomes.append(outcome)
                await asyncio.sleep(0.01)
            insert_case_result(
                run_id,
                case,
                _aggregate_trials(case, trial_outcomes),
            )
        results = list_case_results(run_id)
        metrics, root_causes = _metrics(results)
        trial_rows = list_trial_results(run_id)
        if any(
            case["input"]["type"] == "support_e2e"
            for case in cases
        ):
            metrics.update(_online_execution_metrics(trial_rows))
        metrics.update(
            {
                "trials": trials,
                "seed": seed,
                "deterministic": deterministic,
                "variance_note": (
                    "确定性任务，重复执行方差为 0"
                    if deterministic
                    else "非确定性任务，按 trials 观察波动"
                ),
            }
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        finish_run(
            run_id,
            metrics=metrics,
            root_causes=root_causes,
            duration_ms=duration_ms,
            deterministic=deterministic,
        )
        if LANGFUSE_EXPORT_ENABLED:
            flush()
        return {"run_id": run_id, "metrics": metrics, "root_causes": root_causes}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        fail_run(run_id, str(exc), duration_ms)
        raise
