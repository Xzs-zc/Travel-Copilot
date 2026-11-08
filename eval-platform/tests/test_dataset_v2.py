from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.core.dataset_schema import load_dataset, stable_split, validate_dataset  # noqa: E402


def test_dataset_v2_schema_distribution_and_split_are_stable() -> None:
    path = PLATFORM_ROOT / "datasets" / "traveling_business_v2.jsonl"
    rows = load_dataset(path)
    assert len(rows) >= 200
    assert len({case.case_key for case in rows}) == len(rows)
    counts = Counter(case.category for case in rows)
    assert counts["transport_policy"] >= 50
    assert counts["agent_contract"] >= 40
    assert counts["support_routing"] >= 30
    assert counts["safety_policy"] >= 30
    assert counts["knowledge_retrieval"] >= 30
    assert counts["trajectory"] >= 15
    assert {"hand", "derived_from_failure", "adversarial", "adapted"} <= {
        case.source for case in rows
    }
    for case in rows:
        assert case.split == stable_split(case.case_key)
    holdout = sum(case.split == "holdout" for case in rows) / len(rows)
    assert 0.15 <= holdout <= 0.25

    summary = validate_dataset(path)
    assert summary["valid"] is True
    assert summary["holdout_ratio"] >= 0.15
