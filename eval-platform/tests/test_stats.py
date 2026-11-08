from __future__ import annotations

import sys
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.core.stats import (  # noqa: E402
    bootstrap_ci,
    mcnemar,
    paired_bootstrap_ci,
    pass_at_k,
    pass_hat_k,
)


def test_pass_metrics_match_manual_small_example() -> None:
    assert pass_at_k(5, 3, 2) == pytest.approx(0.9)
    assert pass_hat_k(5, 3, 2) == pytest.approx(0.3)


def test_bootstrap_is_reproducible_and_zero_variance_is_none() -> None:
    values = [0.0, 0.2, 0.5, 0.8, 1.0]
    first = bootstrap_ci(values, lambda sample: sum(sample) / len(sample), seed=7)
    second = bootstrap_ci(values, lambda sample: sum(sample) / len(sample), seed=7)
    assert first == second
    assert bootstrap_ci([1.0, 1.0, 1.0], lambda sample: sum(sample) / len(sample)) is None
    assert paired_bootstrap_ci([0, 0, 1, 1], [1, 1, 1, 1], seed=7) is not None


def test_mcnemar_known_small_table() -> None:
    assert mcnemar(10, 2) == pytest.approx(0.038574, abs=1e-6)
    assert mcnemar(0, 0) == 1.0
