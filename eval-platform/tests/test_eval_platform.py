from __future__ import annotations

import sys
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.dataset_builder import build_cases  # noqa: E402
from platform_backend import db  # noqa: E402
from platform_backend.runner import run_dataset  # noqa: E402
from platform_backend.config import DATABASE_URL  # noqa: E402


def test_dataset_is_distinct_and_balanced() -> None:
    cases = build_cases()
    assert len(cases) == 60
    assert len({case["case_key"] for case in cases}) == 60
    assert {case["category"] for case in cases} == {
        "agent_contract",
        "attraction_evidence",
        "knowledge_retrieval",
        "safety_policy",
        "support_routing",
        "transport_policy",
    }


@pytest.mark.asyncio
async def test_candidate_run_completes_all_cases() -> None:
    assert DATABASE_URL.startswith("postgresql://")
    db.init_db()
    dataset = db.seed_dataset_from_jsonl("traveling-business-v1")
    cases = db.get_cases(dataset["id"])
    run = db.create_run(
        dataset_id=dataset["id"],
        name="test candidate",
        variant="candidate-current",
    )
    result = await run_dataset(run_id=run["id"], cases=cases, variant="candidate-current")
    assert result["metrics"]["total"] == 60
    assert result["metrics"]["passed"] == 60
    assert result["metrics"]["pass_rate"] == 100.0
    with db.connection() as connection:
        connection.execute("DELETE FROM trace_spans WHERE run_id = %s", (run["id"],))
        connection.execute("DELETE FROM trial_results WHERE run_id = %s", (run["id"],))
        connection.execute("DELETE FROM case_results WHERE run_id = %s", (run["id"],))
        connection.execute("DELETE FROM runs WHERE id = %s", (run["id"],))
