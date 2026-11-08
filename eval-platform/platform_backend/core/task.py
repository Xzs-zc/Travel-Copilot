from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters import support, travel_rules
from ..adapters import support_online
from .system_config import SystemConfig
from .tracing import span


@dataclass
class TaskOutput:
    actual: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


async def _execute_task(case: dict[str, Any], config: SystemConfig) -> TaskOutput:
    input_data = case["input"]
    input_type = input_data["type"]

    if input_type == "transport_recommendation":
        route = travel_rules.build_route(input_data)
        with span(
            "adapter.transport.recommend",
            "policy",
            input={
                "train": input_data["train"],
                "flight_available": input_data.get("flight_available", False),
            },
        ) as adapter_span:
            recommendation = travel_rules.recommend(
                {"mode": input_data.get("candidate_mode", "")},
                route,
                input_data["train"],
                travel_rules.build_flights(input_data),
            )
            adapter_span.update(output=recommendation)
        return TaskOutput(
            actual={
                **recommendation,
                "candidate_mode": input_data.get("candidate_mode", ""),
            }
        )

    if input_type == "argument_validation":
        with span(
            "adapter.validators.arguments",
            "validator",
            input={"tool": input_data["tool"], "arguments": input_data["arguments"]},
        ) as adapter_span:
            normalized, errors = travel_rules.validate_arguments(
                config,
                input_data["tool"],
                input_data["arguments"],
            )
            adapter_span.update(
                output={"normalized": normalized, "errors": errors}
            )
        return TaskOutput(
            actual={
                "normalized": normalized,
                "errors": errors,
                "blocked": normalized is None,
            }
        )

    if input_type == "tool_result":
        with span(
            "adapter.validators.tool_result",
            "validator",
            input={"tool": input_data["tool"], "result": input_data["result"]},
        ) as adapter_span:
            result = travel_rules.validate_tool_result(
                config,
                input_data["tool"],
                input_data["result"],
                input_data["arguments"],
            )
            adapter_span.update(output=result)
        return TaskOutput(
            actual=result
        )

    if input_type == "final_validation":
        with span(
            "adapter.validators.final_result",
            "validator",
            input={"final_tool": input_data["final_tool"]},
        ) as adapter_span:
            result = travel_rules.validate_final_result(
                config,
                input_data["final_tool"],
                input_data["data"],
            )
            adapter_span.update(output=result)
        return TaskOutput(
            actual=result
        )

    if input_type == "support_route":
        with span(
            "adapter.support.route",
            "router",
            input={"message": input_data["message"]},
        ) as adapter_span:
            result = support.route_message(
                input_data["message"],
                input_data["context"],
            )
            adapter_span.update(output=result)
        return TaskOutput(
            actual=result
        )

    if input_type == "knowledge_retrieval":
        if config.rag_mode == "off":
            result = {"documents": [], "retrieval": "disabled", "error": ""}
        else:
            with span(
                "adapter.rag.search",
                "retriever",
                input={
                    "query": input_data["query"],
                    "routes": input_data["routes"],
                    "mode": config.rag_mode,
                },
            ) as adapter_span:
                result = await support.search_knowledge(
                    input_data["query"],
                    routes=input_data["routes"],
                    limit=15,
                    mode="hybrid" if config.rag_mode == "hybrid" else "lexical",
                )
                adapter_span.update(output=result)
        documents = result.get("documents") or []
        return TaskOutput(
            actual={
                "documents": [
                    {
                        "doc_id": item.get("doc_id"),
                        "section": item.get("section"),
                        "score": item.get("score"),
                    }
                    for item in documents
                ],
                "retrieval": result.get("retrieval"),
                "error": result.get("error"),
                "rag_mode": config.rag_mode,
            }
        )

    if input_type == "safety_tool":
        with span(
            "adapter.policy.validate_tool",
            "guardrail",
            input={"tool": input_data["tool"], "arguments": input_data["arguments"]},
        ) as adapter_span:
            normalized, errors = travel_rules.validate_arguments(
                config,
                input_data["tool"],
                input_data["arguments"],
            )
            adapter_span.update(
                output={"normalized": normalized, "errors": errors}
            )
        return TaskOutput(
            actual={
                "blocked": normalized is None and bool(errors),
                "normalized": normalized,
                "errors": errors,
            }
        )

    if input_type == "date_delta":
        with span(
            "adapter.replan.delta",
            "replanner",
            input={"before": input_data["before"], "after": input_data["after"]},
        ) as adapter_span:
            result = travel_rules.build_replan_delta(
                "transport_plan",
                input_data["before"],
                input_data["after"],
            )
            adapter_span.update(output=result)
        return TaskOutput(
            actual=result
        )

    if input_type == "trace_audit":
        trace = input_data.get("trace") or []
        tool_calls = [
            str(item.get("tool") or "")
            for item in trace
            if isinstance(item, dict)
        ]
        return TaskOutput(
            actual={
                "tool_calls": tool_calls,
                "steps": len(trace),
                "duplicate_calls": len(tool_calls) - len(set(tool_calls)),
            }
        )

    if input_type == "support_e2e":
        actual = await support_online.run_support_case(
            {
                **input_data,
                "case_key": case["case_key"],
                "difficulty": case.get("difficulty", "medium"),
            }
        )
        return TaskOutput(actual=actual)

    raise ValueError(f"Unsupported case type: {input_type}")


async def execute_task(case: dict[str, Any], config: SystemConfig) -> TaskOutput:
    with span(
        f"task.{case['input']['type']}",
        "task",
        input=case["input"],
        metadata={"system_config_id": config.id},
    ) as task_span:
        output = await _execute_task(case, config)
        task_span.update(output=output.actual)
        return output
