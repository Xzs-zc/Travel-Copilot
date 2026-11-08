from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db  # noqa: E402
from platform_backend.runner import run_dataset  # noqa: E402


@pytest.mark.asyncio
async def test_baselines_only_degrade_their_affected_categories() -> None:
    db.init_db()
    db.seed_system_configs()
    dataset = db.seed_dataset_from_jsonl("traveling-business-v1")
    cases = db.get_cases(dataset["id"])
    run_ids: list[str] = []
    metrics: dict[str, dict] = {}
    try:
        for config_id in (
            "baseline-no-rag",
            "baseline-basic-validators",
            "candidate-current",
        ):
            run = db.create_run(
                dataset_id=dataset["id"],
                name=f"{config_id}-{uuid.uuid4().hex[:8]}",
                variant=config_id,
            )
            run_ids.append(run["id"])
            result = await run_dataset(
                run_id=run["id"],
                cases=cases,
                variant=config_id,
            )
            metrics[config_id] = result["metrics"]["categories"]

        candidate = metrics["candidate-current"]
        no_rag = metrics["baseline-no-rag"]
        basic = metrics["baseline-basic-validators"]

        assert no_rag["knowledge_retrieval"]["pass_rate"] < candidate["knowledge_retrieval"]["pass_rate"]
        for category in candidate:
            if category != "knowledge_retrieval":
                assert no_rag[category]["pass_rate"] == candidate[category]["pass_rate"]

        affected_by_validators = {
            "agent_contract",
            "attraction_evidence",
            "safety_policy",
        }
        for category in affected_by_validators:
            assert basic[category]["pass_rate"] < candidate[category]["pass_rate"]
        for category in candidate:
            if category not in affected_by_validators:
                assert basic[category]["pass_rate"] == candidate[category]["pass_rate"]
    finally:
        with db.connection() as conn:
            for run_id in run_ids:
                conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM case_results WHERE run_id = %s", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))
