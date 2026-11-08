from __future__ import annotations

import sys
import uuid
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db  # noqa: E402
from platform_backend.core.gate import evaluate_gate  # noqa: E402


def _create_finished_run(
    *,
    dataset_id: str,
    pass_rate: float,
    categories: dict[str, dict] | None = None,
    extra_metrics: dict | None = None,
) -> str:
    run = db.create_run(
        dataset_id=dataset_id,
        name=f"gate-test-{uuid.uuid4().hex[:8]}",
        variant="candidate-current",
        split="dev",
    )
    db.finish_run(
        run["id"],
        metrics={
            "pass_rate": pass_rate,
            "critical_failures": 0,
            "p95_duration_ms": 100,
            "avg_cost_usd": 0,
            "categories": categories or {},
            **(extra_metrics or {}),
        },
        root_causes={},
        duration_ms=100,
        deterministic=True,
    )
    return run["id"]


def test_gate_passes_and_catches_degraded_or_missing_data() -> None:
    db.init_db()
    db.seed_system_configs()
    dataset = db.seed_dataset_from_jsonl("traveling-business-v1")
    baseline = _create_finished_run(dataset_id=dataset["id"], pass_rate=90)
    candidate = _create_finished_run(
        dataset_id=dataset["id"],
        pass_rate=100,
        categories={
            "safety_policy": {"total": 5, "passed": 5, "pass_rate": 100},
            "agent_contract": {"total": 5, "passed": 5, "pass_rate": 100},
        },
    )
    config = {
        "dataset": dataset["id"],
        "split": "dev",
        "overall_pass_rate_min": 0.95,
        "critical_failures_max": 0,
        "category_pass_rate_min": {
            "safety_policy": 1.0,
            "agent_contract": 0.98,
        },
        "regression_vs_baseline_max": 0,
        "latency_p95_ms_max": 15000,
        "cost_usd_per_case_max": None,
    }
    try:
        assert evaluate_gate(
            run_id=candidate,
            baseline_run_id=baseline,
            config=config,
        )["passed"] is True

        degraded = _create_finished_run(dataset_id=dataset["id"], pass_rate=80)
        missing = _create_finished_run(dataset_id=dataset["id"], pass_rate=100)
        assert evaluate_gate(
            run_id=degraded,
            baseline_run_id=baseline,
            config=config,
        )["passed"] is False
        missing_result = evaluate_gate(
            run_id=missing,
            baseline_run_id=baseline,
            config=config,
        )
        assert missing_result["passed"] is False
        assert any(rule["status"] == "unable" for rule in missing_result["rules"])
        run_ids = [candidate, degraded, missing]
    finally:
        with db.connection() as conn:
            for run_id in [baseline, candidate, degraded, missing]:
                conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM case_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


def test_online_gate_uses_error_rate_and_pass_hat() -> None:
    db.init_db()
    db.seed_system_configs()
    dataset = db.seed_dataset_from_jsonl("online-support-e2e-v1")
    passing = _create_finished_run(
        dataset_id=dataset["id"],
        pass_rate=100,
        extra_metrics={
            "error_rate": 0.0,
            "pass_hat_k": 1.0,
            "p95_duration_ms": 5000,
        },
    )
    degraded = _create_finished_run(
        dataset_id=dataset["id"],
        pass_rate=90,
        extra_metrics={
            "error_rate": 0.1,
            "pass_hat_k": 0.75,
            "p95_duration_ms": 20000,
        },
    )
    config = {
        "error_rate_max": 0.05,
        "pass_hat_k_min": 0.85,
        "latency_p95_ms_max": 15000,
        "cost_usd_per_case_max": None,
    }
    try:
        assert evaluate_gate(
            run_id=passing,
            baseline_run_id="",
            config=config,
        )["passed"] is True
        failed = evaluate_gate(
            run_id=degraded,
            baseline_run_id="",
            config=config,
        )
        assert failed["passed"] is False
        assert {
            rule["name"]
            for rule in failed["rules"]
            if rule["status"] == "fail"
        } >= {"error_rate", "pass_hat_k", "latency_p95_ms"}
    finally:
        with db.connection() as conn:
            for run_id in [passing, degraded]:
                conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM case_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))
