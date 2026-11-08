from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .. import db


def load_gate_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_gate_config")
    return payload


def _rule(
    name: str,
    *,
    status: str,
    actual: Any = None,
    expected: Any = None,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "actual": actual,
        "expected": expected,
        "detail": detail,
    }


def evaluate_gate(
    *,
    run_id: str,
    baseline_run_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    run = db.get_run(run_id)
    baseline = db.get_run(baseline_run_id)
    rules = []
    if run is None:
        rules.append(_rule("run_exists", status="unable", detail="run_not_found"))
        return {"passed": False, "run_id": run_id, "baseline_run_id": baseline_run_id, "rules": rules}
    if baseline_run_id and baseline is None:
        rules.append(_rule("baseline_exists", status="unable", detail="baseline_not_found"))
    dataset_expected = config.get("dataset")
    if dataset_expected is None:
        rules.append(_rule("dataset", status="skip", actual=run["dataset_id"]))
    else:
        rules.append(
            _rule(
                "dataset",
                status="pass" if run["dataset_id"] == dataset_expected else "fail",
                actual=run["dataset_id"],
                expected=dataset_expected,
            )
        )
    split_expected = config.get("split")
    if split_expected:
        rules.append(
            _rule(
                "split",
                status="pass" if run.get("split") == split_expected else "fail",
                actual=run.get("split"),
                expected=split_expected,
            )
        )
    metrics = run.get("metrics") or {}
    minimum = config.get("overall_pass_rate_min")
    actual_rate = metrics.get("pass_rate")
    if minimum is None:
        rules.append(_rule("overall_pass_rate", status="skip", actual=actual_rate))
    elif actual_rate is None:
        rules.append(_rule("overall_pass_rate", status="unable", actual=actual_rate, expected=minimum))
    else:
        actual_fraction = float(actual_rate) / 100
        rules.append(
            _rule(
                "overall_pass_rate",
                status="pass" if actual_fraction >= float(minimum) else "fail",
                actual=actual_fraction,
                expected=minimum,
            )
        )
    critical_max = config.get("critical_failures_max")
    critical = metrics.get("critical_failures")
    if critical_max is None:
        rules.append(_rule("critical_failures", status="skip", actual=critical))
    elif critical is None:
        rules.append(_rule("critical_failures", status="unable", actual=critical, expected=critical_max))
    else:
        rules.append(
            _rule(
                "critical_failures",
                status="pass" if int(critical) <= int(critical_max) else "fail",
                actual=critical,
                expected=critical_max,
            )
        )
    error_rate_max = config.get("error_rate_max")
    actual_error_rate = metrics.get("error_rate")
    if error_rate_max is None:
        rules.append(
            _rule("error_rate", status="skip", actual=actual_error_rate)
        )
    elif actual_error_rate is None:
        rules.append(
            _rule(
                "error_rate",
                status="unable",
                actual=None,
                expected=error_rate_max,
            )
        )
    else:
        rules.append(
            _rule(
                "error_rate",
                status=(
                    "pass"
                    if float(actual_error_rate) <= float(error_rate_max)
                    else "fail"
                ),
                actual=actual_error_rate,
                expected=error_rate_max,
            )
        )
    pass_hat_min = config.get("pass_hat_k_min")
    actual_pass_hat = metrics.get("pass_hat_k")
    if pass_hat_min is None:
        rules.append(
            _rule("pass_hat_k", status="skip", actual=actual_pass_hat)
        )
    elif actual_pass_hat is None:
        rules.append(
            _rule(
                "pass_hat_k",
                status="unable",
                actual=None,
                expected=pass_hat_min,
            )
        )
    else:
        rules.append(
            _rule(
                "pass_hat_k",
                status=(
                    "pass"
                    if float(actual_pass_hat) >= float(pass_hat_min)
                    else "fail"
                ),
                actual=actual_pass_hat,
                expected=pass_hat_min,
            )
        )
    categories = metrics.get("categories")
    for category, threshold in (config.get("category_pass_rate_min") or {}).items():
        if not isinstance(categories, dict) or category not in categories:
            rules.append(_rule(f"category:{category}", status="unable", expected=threshold))
            continue
        actual = float(categories[category]["pass_rate"]) / 100
        rules.append(
            _rule(
                f"category:{category}",
                status="pass" if actual >= float(threshold) else "fail",
                actual=actual,
                expected=threshold,
            )
        )
    regression_max = config.get("regression_vs_baseline_max")
    if baseline is None or regression_max is None:
        if regression_max is not None:
            rules.append(
                _rule(
                    "regression_vs_baseline",
                    status="unable",
                    expected=regression_max,
                    detail="baseline_not_found",
                )
            )
    else:
        comparison = db.compare_runs(baseline_run_id, run_id)
        regressions = comparison["summary"]["regressions"]
        rules.append(
            _rule(
                "regression_vs_baseline",
                status="pass" if regressions <= int(regression_max) else "fail",
                actual=regressions,
                expected=regression_max,
            )
        )
    latency_max = config.get("latency_p95_ms_max")
    p95 = metrics.get("p95_duration_ms")
    if latency_max is None:
        rules.append(_rule("latency_p95_ms", status="skip", actual=p95, expected=None))
    elif p95 is None:
        rules.append(_rule("latency_p95_ms", status="unable", actual=None, expected=latency_max))
    else:
        rules.append(
            _rule(
                "latency_p95_ms",
                status="pass" if float(p95) <= float(latency_max) else "fail",
                actual=p95,
                expected=latency_max,
            )
        )
    cost_max = config.get("cost_usd_per_case_max")
    avg_cost = metrics.get("avg_cost_usd")
    if cost_max is None:
        rules.append(_rule("cost_usd_per_case", status="skip", actual=avg_cost, expected=None))
    elif avg_cost is None:
        rules.append(_rule("cost_usd_per_case", status="unable", actual=None, expected=cost_max))
    else:
        rules.append(
            _rule(
                "cost_usd_per_case",
                status="pass" if float(avg_cost) <= float(cost_max) else "fail",
                actual=avg_cost,
                expected=cost_max,
            )
        )
    passed = all(rule["status"] in {"pass", "skip"} for rule in rules)
    return {
        "passed": passed,
        "run_id": run_id,
        "baseline_run_id": baseline_run_id,
        "rules": rules,
    }


def gate_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Evaluation Gate",
        "",
        f"- Run: `{result.get('run_id', '')}`",
        f"- Baseline: `{result.get('baseline_run_id', '')}`",
        f"- Result: **{'PASS' if result['passed'] else 'FAIL'}**",
        "",
        "| Rule | Status | Actual | Expected | Detail |",
        "|---|---|---:|---:|---|",
    ]
    for rule in result["rules"]:
        lines.append(
            "| {name} | {status} | {actual} | {expected} | {detail} |".format(
                name=rule["name"],
                status=rule["status"],
                actual=rule.get("actual", ""),
                expected=rule.get("expected", ""),
                detail=str(rule.get("detail", "")).replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"
