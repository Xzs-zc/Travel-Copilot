from __future__ import annotations

import sys
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.core.attribution import attribute_failure  # noqa: E402
from platform_backend.graders import grade_case  # noqa: E402


def _retrieval_case() -> dict:
    return {
        "case_key": "ATTR-RAG",
        "name": "retrieval",
        "category": "knowledge_retrieval",
        "input": {"type": "knowledge_retrieval", "query": "q", "routes": ["site_guide"]},
        "expected": {"doc_id": "doc-a", "section": "target"},
    }


def test_rag_noise_miss_and_pass_are_distinguished() -> None:
    case = _retrieval_case()
    documents = [
        {"doc_id": f"doc-{index}", "section": "other"}
        for index in range(7)
    ] + [{"doc_id": "doc-a", "section": "target"}]
    failed = attribute_failure(
        case,
        {"documents": documents, "retrieval": "hybrid", "error": ""},
    )
    assert failed.primary == "retrieval_noise"
    assert failed.rule_id == "rag.noise.gold_rank_gt_5"
    assert failed.evidence["gold_rank"] == 8

    missed = attribute_failure(
        case,
        {"documents": documents[:-1], "retrieval": "hybrid", "error": ""},
    )
    assert missed.primary == "retrieval_miss"
    assert missed.evidence["gold_rank"] is None

    passing_documents = [
        {"doc_id": "doc-0", "section": "other"},
        {"doc_id": "doc-1", "section": "other"},
        {"doc_id": "doc-a", "section": "target"},
    ]
    result = grade_case(
        case,
        {"documents": passing_documents, "retrieval": "hybrid", "error": ""},
    )
    assert result.passed is True
    assert result.attribution is None


def test_route_transport_policy_validator_and_trace_rules() -> None:
    route = attribute_failure(
        {
            "category": "support_routing",
            "input": {"type": "support_route"},
            "expected": {"primary_agent": "policy"},
        },
        {"primary_agent": "site_guide"},
    )
    assert route.primary == "routing_error"
    assert "policy_violation" in route.secondary

    transport = attribute_failure(
        {
            "category": "transport_policy",
            "input": {
                "type": "transport_recommendation",
                "route": {"distance_m": 100},
                "train": {"min_duration_minutes": 60},
            },
            "expected": {"mode_in": ["high_speed_rail"]},
        },
        {"mode": "flight", "candidate_mode": "flight"},
    )
    assert transport.primary == "policy_violation"
    assert transport.rule_id == "transport.policy.invalid_candidate_mode"

    validator = attribute_failure(
        {
            "category": "agent_contract",
            "input": {"type": "argument_validation"},
            "expected": {"ok": False},
        },
        {"normalized": {"origin": "x"}},
    )
    assert validator.primary == "validator_gap"
    assert validator.evidence["direction"] == "false_accept"

    leak = attribute_failure(
        {
            "category": "agent_contract",
            "input": {"type": "tool_result"},
            "expected": {"ok": False, "errors_contains": ["train_code_leak"]},
        },
        {"ok": True, "errors": [], "warnings": []},
    )
    assert leak.primary == "policy_violation"
    assert "validator_gap" in leak.secondary

    trace = attribute_failure(
        {
            "category": "trajectory",
            "input": {"type": "trace_audit"},
            "expected": {
                "required_tools": ["query_train"],
                "forbidden_tools": ["pay_order"],
                "max_steps": 2,
            },
        },
        {"tool_calls": ["pay_order"], "steps": 1},
    )
    assert trace.primary == "tool_selection_error"
    assert "policy_violation" in trace.secondary
    assert trace.evidence["trace_source"] == "fixture"


def test_same_category_can_have_multiple_primary_causes() -> None:
    case = _retrieval_case()
    noise = attribute_failure(
        case,
        {
            "documents": [
                {"doc_id": f"doc-{index}", "section": "other"}
                for index in range(8)
            ]
            + [{"doc_id": "doc-a", "section": "target"}],
            "retrieval": "hybrid",
        },
    )
    miss = attribute_failure(
        case,
        {"documents": [], "retrieval": "hybrid"},
    )
    assert {noise.primary, miss.primary} == {"retrieval_noise", "retrieval_miss"}
