from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend import config, db  # noqa: E402
from platform_backend.main import app  # noqa: E402
from platform_backend.runner import run_dataset  # noqa: E402


def _delete_run(run_id: str) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM case_results WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


def test_api_validation_cancel_and_restart_recovery(tmp_path: Path) -> None:
    with TestClient(app) as client:
        datasets = client.get("/api/datasets").json()["items"]
        assert datasets
        dataset_id = datasets[0]["id"]

        assert client.get("/api/unknown").status_code == 404
        assert client.get("/api/not-a-route").headers["content-type"].startswith(
            "application/json"
        )

        invalid_variant = client.post(
            "/api/runs",
            json={"dataset_id": dataset_id, "variant": "unknown"},
        )
        assert invalid_variant.status_code == 422

        invalid_baseline = client.post(
            "/api/runs",
            json={"dataset_id": dataset_id, "baseline_run_id": "missing"},
        )
        assert invalid_baseline.status_code == 422

        started = client.post(
            "/api/runs",
            json={
                "dataset_id": dataset_id,
                "name": f"cancel-{uuid.uuid4().hex[:8]}",
                "variant": "baseline-no-rag",
            },
        )
        assert started.status_code == 200
        run_id = started.json()["id"]
        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        deadline = time.monotonic() + 2
        while client.get(f"/api/runs/{run_id}").json()["status"] != "cancelled":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        _delete_run(run_id)

        interrupted = db.create_run(
            dataset_id=dataset_id,
            name=f"interrupted-{uuid.uuid4().hex[:8]}",
            variant="candidate-current",
        )
        db.update_run_status(interrupted["id"], "running")
        assert db.interrupt_incomplete_runs() >= 1
        assert db.get_run(interrupted["id"])["status"] == "interrupted"
        _delete_run(interrupted["id"])


def test_dataset_hash_change_versions_cases_without_mutating_old_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dataset_id = f"version-test-{uuid.uuid4().hex[:8]}"
    dataset_path = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "manifest.json"
    original = json.loads(
        (PLATFORM_ROOT / "datasets" / "traveling_business_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    original["name"] = f"version-one-{uuid.uuid4().hex[:8]}"
    dataset_path.write_text(json.dumps(original, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "name": "Version test dataset",
                "version": "1.0.0",
                "description": "test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(config, "MANIFEST_PATH", manifest_path)

    db.init_db()
    db.seed_system_configs()
    first = db.seed_dataset_from_jsonl(dataset_id)
    first_cases = db.get_cases(dataset_id)
    first_run = db.create_run(
        dataset_id=dataset_id,
        name="version one run",
        variant="candidate-current",
    )

    import asyncio

    asyncio.run(
        run_dataset(
            run_id=first_run["id"],
            cases=first_cases,
            variant="candidate-current",
        )
    )
    old_result = db.get_case_result(first_run["id"], original["case_key"])
    assert old_result is not None

    changed = dict(original)
    changed["name"] = f"version-two-{uuid.uuid4().hex[:8]}"
    dataset_path.write_text(json.dumps(changed, ensure_ascii=False) + "\n", encoding="utf-8")
    second = db.seed_dataset_from_jsonl(dataset_id)
    assert len(second["versions"]) == 2
    assert first["source_hash"] != second["source_hash"]
    assert db.get_cases(dataset_id)[0]["name"] == changed["name"]
    assert db.get_case_result(first_run["id"], original["case_key"]) == old_result
    assert db.get_run(first_run["id"])["dataset_version_hash"] != db.get_run(
        db.create_run(
            dataset_id=dataset_id,
            name="version two run",
            variant="candidate-current",
        )["id"]
    )["dataset_version_hash"]

    with db.connection() as conn:
        second_run_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM runs WHERE dataset_id = %s", (dataset_id,)
            ).fetchall()
        ]
        for run_id in second_run_ids:
            conn.execute("DELETE FROM trace_spans WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM trial_results WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM case_results WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM runs WHERE dataset_id = %s", (dataset_id,))
        conn.execute("DELETE FROM cases WHERE dataset_id = %s", (dataset_id,))
        conn.execute("DELETE FROM dataset_versions WHERE dataset_id = %s", (dataset_id,))
        conn.execute("DELETE FROM datasets WHERE id = %s", (dataset_id,))
