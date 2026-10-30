import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "evals" / "runtime300"


def read_cases(name):
    return [
        json.loads(line)
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_runtime300_distribution_and_identity():
    expected = {
        "support_50.jsonl": 50,
        "rag_100.jsonl": 100,
        "reliability_50.jsonl": 50,
        "attraction_50.jsonl": 50,
        "travel_50.jsonl": 50,
    }
    all_ids = set()
    for filename, count in expected.items():
        rows = read_cases(filename)
        ids = {row["id"] for row in rows}
        assert len(rows) == count
        assert len(ids) == count
        assert not all_ids.intersection(ids)
        all_ids.update(ids)
    assert len(all_ids) == 300


def test_runtime300_target_coverage():
    reliability = read_cases("reliability_50.jsonl")
    assert Counter(row["kind"] for row in reliability) == {
        "idempotency": 5,
        "task_state": 5,
        "retry_recovery": 5,
        "stale_worker": 5,
        "queue_consumer": 5,
        "sse_replay": 5,
        "cache_ttl": 5,
        "rate_limit": 5,
        "circuit_breaker": 5,
        "tenant_isolation": 5,
    }
    assert sum(row["mode"] == "online" for row in read_cases("attraction_50.jsonl")) == 5
    assert sum(row["mode"] == "online" for row in read_cases("travel_50.jsonl")) == 5
    assert sum(row["block"] == "e2e" for row in read_cases("support_50.jsonl")) == 25
