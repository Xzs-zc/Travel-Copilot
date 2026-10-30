from pathlib import Path

from backend.app.evaluation.travel_v2 import (
    LIVE_CASE_IDS,
    OFFLINE_CATEGORY_COUNTS,
    TravelEvalV2Runner,
)


def test_travel_v2_suite_shape_is_frozen():
    runner = TravelEvalV2Runner(
        base_url="http://127.0.0.1:8765",
        output_root=Path("/tmp/travel-v2-tests"),
    )

    assert sum(OFFLINE_CATEGORY_COUNTS.values()) == 60
    assert len(runner._e2e_specs()) == 15
    assert len(LIVE_CASE_IDS) == 12
    assert len(set(LIVE_CASE_IDS)) == 12


def test_travel_v2_tools_schema_deterministic_cases_pass():
    runner = TravelEvalV2Runner(
        base_url="http://127.0.0.1:8765",
        output_root=Path("/tmp/travel-v2-tests"),
    )
    results = runner._run_tools_schema()

    assert len(results) == 15
    assert all(item.passed for item in results)


def test_travel_v2_percentile():
    assert TravelEvalV2Runner._percentile([10, 20, 30, 40], 50) == 20
    assert TravelEvalV2Runner._percentile([10, 20, 30, 40], 95) == 40
