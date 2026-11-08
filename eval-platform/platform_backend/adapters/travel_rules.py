from __future__ import annotations

from typing import Any

from backend.app.agent.public_api import (
    recommend_transport,
    replan_delta,
    validate_final_output,
    validate_tool_arguments,
    validate_tool_output,
)
from backend.app.schemas import FlightOption, RouteMapInfo, SourceRef

from ..core.system_config import SystemConfig


def build_route(input_data: dict[str, Any]) -> RouteMapInfo:
    route = input_data["route"]
    return RouteMapInfo(
        origin_name="评测出发地",
        destination_name="评测目的地",
        origin_location=route["origin_location"],
        destination_location=route["destination_location"],
        distance_m=int(route["distance_m"]),
        duration_s=int(route["duration_s"]),
        source=SourceRef(title="评测高德路线", provider="高德地图"),
    )


def build_flights(input_data: dict[str, Any]) -> list[FlightOption]:
    if not input_data.get("flight_available"):
        return []
    return [
        FlightOption(
            airline="评测航空",
            price=760,
            duration_minutes=150,
            departure_time="09:00",
            arrival_time="11:30",
            departure_airport="A",
            arrival_airport="B",
            stops=0,
            source=SourceRef(title="评测航班", provider="Google Flights / SerpAPI"),
        )
    ]


def recommend(
    llm: dict[str, Any],
    route: RouteMapInfo,
    train_summary: dict[str, Any],
    flights: list[FlightOption],
) -> dict[str, Any]:
    recommendation = recommend_transport(llm, route, train_summary, flights)
    return {
        "mode": recommendation.mode,
        "summary": recommendation.summary,
        "reason": recommendation.reason,
    }


def validate_arguments(
    config: SystemConfig,
    tool: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    if config.validators == "strict":
        return validate_tool_arguments(tool, arguments)
    if config.validators == "basic":
        return (dict(arguments), []) if isinstance(arguments, dict) else (None, ["arguments_not_object"])
    return dict(arguments) if isinstance(arguments, dict) else None, []


def validate_tool_result(
    config: SystemConfig,
    tool: str,
    result: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if config.validators != "strict":
        return {"ok": True, "errors": [], "warnings": [], "evidence": []}
    return validate_tool_output(tool, result, arguments).model_dump(mode="json")


def validate_final_result(
    config: SystemConfig,
    final_tool: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    if config.validators != "strict":
        return {"ok": True, "errors": [], "warnings": [], "evidence": []}
    return validate_final_output(final_tool, data).model_dump(mode="json")


def build_replan_delta(
    task: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    return replan_delta(task, before, after)
