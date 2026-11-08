from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import db  # noqa: E402
from platform_backend.main import app  # noqa: E402


def test_failure_review_requires_corrected_cause_and_excludes_fixtures() -> None:
    db.init_db()
    db.seed_system_configs()
    dataset = db.seed_dataset_from_jsonl("traveling-business-v1")
    run = db.create_run(
        dataset_id=dataset["id"],
        name=f"review-{uuid.uuid4().hex[:8]}",
        variant="candidate-current",
    )
    case = {
        "case_key": "ATTR-REVIEW",
        "name": "review case",
        "category": "knowledge_retrieval",
        "severity": "high",
        "input": {
            "type": "knowledge_retrieval",
            "query": "q",
            "routes": ["site_guide"],
        },
        "expected": {"doc_id": "doc-a", "section": "target"},
    }
    outcome = {
        "passed": False,
        "score": 0,
        "duration_ms": 0,
        "actual": {"documents": [], "retrieval": "hybrid"},
        "failures": ["gold_not_found"],
        "root_causes": ["retrieval_miss"],
        "attribution": {
            "primary": "retrieval_miss",
            "secondary": [],
            "evidence": {"list_len": 0},
            "method": "rule",
            "rule_id": "rag.miss.gold_absent",
            "version": "attr-v1",
        },
        "metrics": {},
        "trace_id": "trace",
    }
    db.insert_case_result(run["id"], case, outcome)
    try:
        with TestClient(app) as client:
            missing = client.post(
                f"/api/runs/{run['id']}/cases/ATTR-REVIEW/failure-review",
                json={"score": 2, "labeler": "owner"},
            )
            assert missing.status_code == 422
            fixture = client.post(
                f"/api/runs/{run['id']}/cases/ATTR-REVIEW/failure-review",
                json={
                    "score": 1,
                    "labeler": "test-fixture",
                    "corrected_cause": "",
                },
            )
            assert fixture.status_code == 200
            clusters = client.get(
                f"/api/runs/{run['id']}/failure-clusters"
            ).json()
            assert clusters["review"]["reviewed"] == 0
            assert clusters["review"]["insufficient"] is True
    finally:
        with db.connection() as conn:
            conn.execute("DELETE FROM human_labels WHERE run_id = %s", (run["id"],))
            conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run["id"],))
            conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run["id"],))
            conn.execute("DELETE FROM case_results WHERE run_id = %s", (run["id"],))
            conn.execute("DELETE FROM runs WHERE id = %s", (run["id"],))
