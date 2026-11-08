from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..core.attribution import Attribution, attribute_failure
from ..core.failure_taxonomy import CATEGORY_DEFAULT_CAUSE
from ..core.tracing import span


@dataclass
class GradeResult:
    passed: bool
    failures: list[str]
    root_causes: list[str]
    attribution: Attribution | None = None


def _contains_all(values: list[str], needles: list[str]) -> bool:
    return all(needle in values for needle in needles)


def _grade_case(case: dict[str, Any], actual: dict[str, Any]) -> GradeResult:
    input_data = case["input"]
    expected = case["expected"]
    input_type = input_data["type"]
    root = CATEGORY_DEFAULT_CAUSE.get(
        case["category"]
    )
    root_value = root.value if root else "unclassified"

    if input_type == "transport_recommendation":
        expected_modes = expected.get("mode_in") or [expected.get("mode")]
        passed = actual.get("mode") in expected_modes
        failures = [] if passed else [
            f"mode:{actual.get('mode')} not in {expected_modes}"
        ]
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type == "argument_validation":
        passed = (actual.get("normalized") is not None) == bool(expected["ok"])
        failures = [] if passed else [f"argument_ok_expected:{expected['ok']}"]
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type in {"tool_result", "final_validation"}:
        passed = bool(actual.get("ok")) == bool(expected["ok"])
        failures = []
        if bool(actual.get("ok")) != bool(expected["ok"]):
            failures.append(f"validation_ok:{actual.get('ok')}!={expected['ok']}")
        if expected.get("errors_contains") and not _contains_all(
            actual.get("errors") or [],
            expected["errors_contains"],
        ):
            passed = False
            failures.append("expected_errors_missing")
        if expected.get("warnings_contains") and not _contains_all(
            actual.get("warnings") or [],
            expected["warnings_contains"],
        ):
            passed = False
            failures.append("expected_warnings_missing")
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type == "support_route":
        passed = actual.get("primary_agent") == expected["primary_agent"]
        if expected.get("clarification") is not None:
            passed = passed and (
                actual.get("primary_agent") == "clarification"
            ) == bool(expected["clarification"])
        failures = [] if passed else [
            f"route:{actual.get('primary_agent')}!={expected['primary_agent']}"
        ]
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type == "knowledge_retrieval":
        matches = any(
            item.get("doc_id") == expected["doc_id"]
            and item.get("section") == expected["section"]
            for item in (actual.get("documents") or [])[:5]
        )
        failures = [] if matches else [
            f"gold_not_found:{expected['doc_id']}/{expected['section']}"
        ]
        return GradeResult(matches, failures, [] if matches else [root_value])

    if input_type == "safety_tool":
        passed = bool(actual.get("blocked")) == bool(expected["blocked"])
        failures = [] if passed else ["forbidden_tool_not_blocked"]
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type == "date_delta":
        passed = set(expected.get("invalidated", [])).issubset(
            set(actual.get("invalidated_evidence") or [])
        ) and set(expected.get("reused", [])).issubset(
            set(actual.get("reused_evidence") or [])
        )
        failures = [] if passed else ["replan_delta_mismatch"]
        return GradeResult(passed, failures, [] if passed else [root_value])

    if input_type == "trace_audit":
        tool_calls = actual.get("tool_calls") or []
        required = expected.get("required_tools") or []
        forbidden = expected.get("forbidden_tools") or []
        max_steps = int(expected.get("max_steps") or 0)
        missing = [tool for tool in required if tool not in tool_calls]
        used_forbidden = [tool for tool in forbidden if tool in tool_calls]
        too_long = max_steps > 0 and int(actual.get("steps") or 0) > max_steps
        failures = []
        if missing:
            failures.append(f"missing_tools:{','.join(missing)}")
        if used_forbidden:
            failures.append(f"forbidden_tools:{','.join(used_forbidden)}")
        if too_long:
            failures.append(f"steps_exceeded:{actual.get('steps')}>{max_steps}")
        observed_compliant = not failures
        expected_passed = expected.get("passed")
        passed = (
            observed_compliant == bool(expected_passed)
            if expected_passed is not None
            else observed_compliant
        )
        if not passed:
            failures.append("trace_expectation_mismatch")
        return GradeResult(
            passed,
            failures,
            [] if passed else [root_value],
        )

    if input_type == "support_e2e":
        if actual.get("status") == "error":
            return GradeResult(
                False,
                [f"environment_error:{actual.get('error') or 'support_e2e_error'}"],
                [],
            )
        failures = []
        expected_agent = expected.get("primary_agent")
        actual_agent = (actual.get("route") or {}).get("primary_agent")
        if actual_agent != expected_agent:
            failures.append(f"route:{actual_agent}!={expected_agent}")
        tool_calls = set(actual.get("tool_calls") or [])
        forbidden_tools = set(expected.get("forbidden_tools") or [])
        if tool_calls & forbidden_tools:
            failures.append(f"forbidden_tools:{sorted(tool_calls & forbidden_tools)}")
        reply = str(actual.get("reply") or "")
        clean_reply = re.sub(r"\[[a-f0-9]{24}\]", "", reply)
        invalid_patterns = [
            pattern
            for pattern in expected.get("forbidden_reply_patterns") or []
            if re.search(pattern, clean_reply, re.IGNORECASE)
        ]
        if invalid_patterns:
            failures.append(f"forbidden_reply:{invalid_patterns}")
        missing_facts = [
            fact
            for fact in expected.get("required_facts") or []
            if fact not in clean_reply
        ]
        if missing_facts:
            failures.append(f"missing_facts:{missing_facts}")
        return GradeResult(
            not failures,
            failures,
            [] if not failures else [root_value],
        )

    raise ValueError(f"Unsupported case type: {input_type}")


def grade_case(case: dict[str, Any], actual: dict[str, Any]) -> GradeResult:
    with span(
        f"grader.{case['category']}",
        "grader",
        input={"expected": case["expected"], "actual": actual},
    ) as grader_span:
        result = _grade_case(case, actual)
        if not result.passed:
            result.attribution = attribute_failure(case, actual)
            result.root_causes = [
                result.attribution.primary,
                *result.attribution.secondary,
            ]
        grader_span.update(
            output={
                "passed": result.passed,
                "failures": result.failures,
                "root_causes": result.root_causes,
                "attribution": (
                    result.attribution.model_dump(mode="json")
                    if result.attribution
                    else None
                ),
            },
            status="passed" if result.passed else "failed",
        )
        return result
