from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .failure_taxonomy import CATEGORY_DEFAULT_CAUSE, FailureCause


class Attribution(BaseModel):
    primary: str
    secondary: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    method: Literal["rule", "judge", "human", "category_fallback"]
    rule_id: str
    version: str = "attr-v1"


def _result(
    primary: FailureCause | str,
    rule_id: str,
    evidence: dict[str, Any],
    *,
    secondary: list[str] | None = None,
    method: Literal["rule", "judge", "human", "category_fallback"] = "rule",
) -> Attribution:
    value = primary.value if isinstance(primary, FailureCause) else primary
    return Attribution(
        primary=value,
        secondary=secondary or [],
        evidence=evidence,
        method=method,
        rule_id=rule_id,
    )


def _normalize_retrieved(items: list[Any]) -> list[str]:
    result = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            if item.get("doc_id") and item.get("section"):
                result.append(f"{item['doc_id']}/{item['section']}")
            elif item.get("section"):
                result.append(str(item["section"]))
    return result


def _gold_sections(case: dict[str, Any]) -> list[str]:
    expected = case.get("expected") or {}
    if expected.get("gold_sections"):
        return [
            str(item)
            if isinstance(item, str)
            else f"{item.get('doc_id')}/{item.get('section')}"
            for item in expected["gold_sections"]
        ]
    if expected.get("doc_id") and expected.get("section"):
        return [f"{expected['doc_id']}/{expected['section']}"]
    return []


def _attribute_retrieval(
    case: dict[str, Any],
    actual: dict[str, Any],
) -> Attribution:
    if actual.get("error") or actual.get("retrieval_error"):
        return _result(
            FailureCause.ENVIRONMENT_ERROR,
            "rag.environment.retrieval_error",
            {"error": actual.get("error") or actual.get("retrieval_error")},
        )
    if actual.get("rag_mode") == "off" or actual.get("retrieval") == "disabled":
        return _result(
            FailureCause.RETRIEVAL_MISS,
            "rag.miss.disabled",
            {"reason": "rag_disabled"},
        )
    gold = _gold_sections(case)
    retrieved = _normalize_retrieved(
        actual.get("documents")
        or actual.get("retrieved_sections")
        or actual.get("retrieved")
        or []
    )
    rank = next(
        (index for index, item in enumerate(retrieved, start=1) if item in gold),
        None,
    )
    if rank is not None and rank > 5:
        return _result(
            FailureCause.RETRIEVAL_NOISE,
            "rag.noise.gold_rank_gt_5",
            {"gold_rank": rank, "list_len": len(retrieved), "gold": gold},
        )
    evidence: dict[str, Any] = {
        "gold_rank": rank,
        "list_len": len(retrieved),
        "gold": gold,
    }
    if len(retrieved) <= 5:
        evidence["rank_unknown_top5_only"] = True
    return _result(
        FailureCause.RETRIEVAL_MISS,
        "rag.miss.gold_absent",
        evidence,
    )


def attribute_failure(
    case: dict[str, Any],
    actual: dict[str, Any],
    *,
    source_root_causes: list[str] | None = None,
) -> Attribution:
    input_data = case.get("input") or {}
    expected = case.get("expected") or {}
    input_type = input_data.get("type", "")
    evidence = {"source_root_causes": source_root_causes or []}

    if input_type == "imported_rag_quality":
        if actual.get("error"):
            return _result(
                FailureCause.ENVIRONMENT_ERROR,
                "rag.quality.environment_error",
                {**evidence, "error": actual.get("error")},
            )
        if actual.get("invalid_citation_ids"):
            return _result(
                FailureCause.CITATION_ERROR,
                "rag.quality.invalid_citation",
                {
                    **evidence,
                    "invalid_citation_ids": actual["invalid_citation_ids"],
                },
            )
        scores = actual.get("scores") or {}
        faithfulness = scores.get("faithfulness")
        if isinstance(faithfulness, int) and faithfulness < 90:
            return _result(
                FailureCause.GENERATION_HALLUCINATION,
                "rag.quality.low_faithfulness",
                {
                    **evidence,
                    "score": faithfulness,
                    "threshold": 90,
                    "judge_calibration": "AI-reviewed labels; weighted_kappa=0.460377; n=39",
                },
                method="judge",
            )
        if actual.get("recall_at_5", 1) < 1:
            result = _attribute_retrieval(case, actual)
            result.evidence.update(evidence)
            return result
        return _result(
            "unclassified",
            "rag.quality.other_failure",
            {
                **evidence,
                "scores": scores,
                "recall_at_5": actual.get("recall_at_5"),
            },
        )

    if input_type in {"knowledge_retrieval", "imported_rag_retrieval"}:
        result = _attribute_retrieval(case, actual)
        result.evidence.update(evidence)
        return result

    if input_type == "support_route":
        expected_agent = expected.get("primary_agent")
        actual_agent = actual.get("primary_agent")
        if expected_agent != actual_agent:
            secondary = ["policy_violation"] if expected_agent == "policy" else []
            return _result(
                FailureCause.ROUTING_ERROR,
                "route.primary_agent_mismatch",
                {
                    **evidence,
                    "expected": expected_agent,
                    "actual": actual_agent,
                },
                secondary=secondary,
            )

    if input_type == "support_e2e":
        if actual.get("status") == "error":
            return _result(
                FailureCause.ENVIRONMENT_ERROR,
                "online.support.execution_error",
                {**evidence, "error": actual.get("error")},
            )
        expected_agent = expected.get("primary_agent")
        actual_agent = (actual.get("route") or {}).get("primary_agent")
        if actual_agent != expected_agent:
            return _result(
                FailureCause.ROUTING_ERROR,
                "online.support.route_mismatch",
                {**evidence, "expected": expected_agent, "actual": actual_agent},
            )
        tool_calls = set(actual.get("tool_calls") or [])
        forbidden = set(expected.get("forbidden_tools") or [])
        if tool_calls & forbidden:
            return _result(
                FailureCause.TOOL_SELECTION_ERROR,
                "online.support.forbidden_tool",
                {**evidence, "forbidden_tools": sorted(tool_calls & forbidden)},
                secondary=["policy_violation"],
            )
        missing_facts = [
            fact for fact in expected.get("required_facts") or []
            if fact not in str(actual.get("reply") or "")
        ]
        if missing_facts:
            return _result(
                FailureCause.GENERATION_HALLUCINATION,
                "online.support.required_fact_missing",
                {**evidence, "missing_facts": missing_facts},
            )
        return _result(
            FailureCause.POLICY_VIOLATION,
            "online.support.reply_policy_violation",
            evidence,
        )

    if input_type == "transport_recommendation":
        expected_modes = expected.get("mode_in") or [expected.get("mode")]
        actual_mode = actual.get("mode")
        candidate = actual.get("candidate_mode")
        if candidate and actual_mode == candidate and actual_mode not in expected_modes:
            return _result(
                FailureCause.POLICY_VIOLATION,
                "transport.policy.invalid_candidate_mode",
                {
                    **evidence,
                    "candidate_mode": candidate,
                    "expected_modes": expected_modes,
                },
            )
        return _result(
            FailureCause.DECISION_RULE_ERROR,
            "transport.decision.rule_mismatch",
            {
                **evidence,
                "actual": actual_mode,
                "expected": expected_modes,
                "distance_m": input_data.get("route", {}).get("distance_m"),
                "rail_minutes": input_data.get("train", {}).get("min_duration_minutes"),
            },
        )

    if input_type == "argument_validation":
        expected_ok = bool(expected.get("ok"))
        actual_ok = actual.get("normalized") is not None
        return _result(
            FailureCause.VALIDATOR_GAP,
            "validator.arguments.direction_mismatch",
            {
                **evidence,
                "direction": "false_accept" if actual_ok and not expected_ok else "false_reject",
            },
        )

    if input_type in {"tool_result", "final_validation"}:
        errors = actual.get("errors") or []
        warnings = actual.get("warnings") or []
        missing = [
            code
            for code in [*(expected.get("errors_contains") or []), *(expected.get("warnings_contains") or [])]
            if code not in errors and code not in warnings
        ]
        if "train_code_leak" in (expected.get("errors_contains") or []):
            return _result(
                FailureCause.POLICY_VIOLATION,
                "validator.policy.train_code_leak",
                {**evidence, "missing_codes": missing},
                secondary=["validator_gap"],
            )
        return _result(
            FailureCause.VALIDATOR_GAP,
            "validator.result.mismatch",
            {**evidence, "missing_codes": missing},
        )

    if input_type == "safety_tool":
        return _result(
            FailureCause.POLICY_VIOLATION,
            "policy.forbidden_tool_not_blocked",
            {**evidence, "tool": input_data.get("tool")},
            secondary=["validator_gap"],
        )

    if input_type == "date_delta":
        missing_invalidated = sorted(
            set(expected.get("invalidated") or [])
            - set(actual.get("invalidated_evidence") or [])
        )
        missing_reused = sorted(
            set(expected.get("reused") or [])
            - set(actual.get("reused_evidence") or [])
        )
        return _result(
            FailureCause.DECISION_RULE_ERROR,
            "replan.delta.mismatch",
            {
                **evidence,
                "missing_invalidated": missing_invalidated,
                "missing_reused": missing_reused,
            },
        )

    if input_type == "trace_audit":
        tool_calls = actual.get("tool_calls") or []
        required = expected.get("required_tools") or []
        forbidden = expected.get("forbidden_tools") or []
        write_tools = {"pay_order", "book_ticket", "read_other_user", "submit_order", "delete_account"}
        secondary = ["policy_violation"] if set(tool_calls) & write_tools else []
        if set(tool_calls) & set(forbidden):
            rule_id = "trace.tool.forbidden"
        elif [tool for tool in required if tool not in tool_calls]:
            rule_id = "trace.tool.required_missing"
        else:
            rule_id = "trace.tool.steps_exceeded"
        return _result(
            FailureCause.TOOL_SELECTION_ERROR,
            rule_id,
            {
                **evidence,
                "tool_calls": tool_calls,
                "required_tools": required,
                "forbidden_tools": forbidden,
                "steps": actual.get("steps"),
                "trace_source": "fixture",
            },
            secondary=secondary,
        )

    if input_type == "imported_support_blind":
        block = input_data.get("block")
        if block == "router":
            return _result(
                FailureCause.ROUTING_ERROR,
                "import.support.router_mismatch",
                evidence,
            )
        if block == "safety":
            return _result(
                FailureCause.POLICY_VIOLATION,
                "import.support.safety_violation",
                evidence,
            )
        if block == "rag":
            return _result(
                FailureCause.RETRIEVAL_MISS,
                "import.support.rag_miss",
                evidence,
            )
        if block == "fault":
            return _result(
                FailureCause.ENVIRONMENT_ERROR,
                "import.support.fault_recovery",
                evidence,
            )

    if input_type == "imported_runtime300":
        failures = json.dumps(actual, ensure_ascii=False).lower()
        if "retrieval" in failures or "rag" in failures:
            return _result(
                FailureCause.RETRIEVAL_MISS,
                "import.runtime.retrieval_failure",
                evidence,
            )
        if "route" in failures or "router" in failures:
            return _result(
                FailureCause.ROUTING_ERROR,
                "import.runtime.routing_failure",
                evidence,
            )

    fallback = CATEGORY_DEFAULT_CAUSE.get(case.get("category"))
    if fallback:
        return _result(
            fallback,
            "category.fallback",
            evidence,
            method="category_fallback",
        )
    return _result(
        "unclassified",
        "attribution.unclassified",
        {**evidence, "checked_input_type": input_type},
    )
