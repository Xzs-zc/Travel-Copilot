from __future__ import annotations

import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.runner import _online_execution_metrics  # noqa: E402


def _trial(status: str, *, case_key: str = "CASE-1", trial: int = 1) -> dict:
    return {
        "case_key": case_key,
        "trial": trial,
        "status": status,
        "duration_ms": 100,
        "metrics": {
            "tokens_total": 10,
            "cost_amount": 0.01,
        },
    }


def test_online_stats_report_pass_at_k_pass_hat_k_and_flaky() -> None:
    metrics = _online_execution_metrics(
        [
            _trial("passed", trial=1),
            _trial("passed", trial=2),
            _trial("failed", trial=3),
        ]
    )
    assert metrics["pass_at_k"] == 1.0
    assert metrics["pass_hat_k"] == 0.0
    assert metrics["pass_at_3"] == 100.0
    assert metrics["pass_hat_3"] == 0.0
    assert metrics["flaky_cases_count"] == 1
    assert metrics["flaky_cases"][0]["case_key"] == "CASE-1"


def test_online_errors_are_excluded_from_pass_rate_denominator() -> None:
    metrics = _online_execution_metrics(
        [
            _trial("passed", trial=1),
            _trial("error", trial=2),
            _trial("error", trial=3),
        ]
    )
    assert metrics["valid_executions"] == 1
    assert metrics["errors"] == 2
    assert metrics["error_rate"] == 2 / 3
    assert metrics["pass_rate"] == 100.0
    assert metrics["flaky_cases_count"] == 0
