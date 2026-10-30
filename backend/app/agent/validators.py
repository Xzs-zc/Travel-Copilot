from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..schemas import SourceRef
from .state import EvidenceItem


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class SearchAmapArgs(StrictArgs):
    keywords: str = Field(min_length=1)
    city: str = ""
    types: str = ""


class AttractionDetailArgs(StrictArgs):
    poi_id: str


class OfficialVerifyArgs(StrictArgs):
    name: str
    url: str
    keywords: list[str] = Field(default_factory=list)


class SearchWebArgs(StrictArgs):
    query: str
    limit: int = Field(default=5, ge=1, le=10)


class WeatherArgs(StrictArgs):
    city: str = Field(min_length=1)


class TicketQueryArgs(StrictArgs):
    origin: str
    destination: str
    travel_date: str

    @field_validator("travel_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class FlightQueryArgs(TicketQueryArgs):
    pass


class RouteArgs(StrictArgs):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)

class HubResolveArgs(RouteArgs):
    radius_m: int = Field(default=120000, ge=10000, le=300000)


class EmptyArgs(StrictArgs):
    pass


ARG_MODELS: dict[str, type[StrictArgs]] = {
    "search_amap": SearchAmapArgs,
    "get_attraction_detail": AttractionDetailArgs,
    "verify_official_page": OfficialVerifyArgs,
    "search_web": SearchWebArgs,
    "get_weather": WeatherArgs,
    "query_train": TicketQueryArgs,
    "query_flight": FlightQueryArgs,
    "resolve_transport_hubs": HubResolveArgs,
    "route_estimate": RouteArgs,
    "get_preferences": EmptyArgs,
}

TRAIN_CODE_PATTERN = re.compile(r"\b[GDCZKT]\d{1,4}\b", re.IGNORECASE)


def validate_arguments(name: str, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    model = ARG_MODELS.get(name)
    if not model:
        return None, [f"unknown_tool:{name}"]
    try:
        return model.model_validate(raw).model_dump(), []
    except ValidationError as exc:
        return None, [f"invalid_arguments:{error['loc'][-1]}:{error['msg']}" for error in exc.errors()]


def validate_tool_result(name: str, result: dict[str, Any], arguments: dict[str, Any]) -> ValidationResult:
    if not isinstance(result, dict) or not result.get("ok"):
        return ValidationResult(ok=False, errors=[str((result or {}).get("error") or "tool_failed")])

    evidence: list[EvidenceItem] = []
    errors: list[str] = []
    warnings: list[str] = []

    if name == "get_attraction_detail":
        poi = result.get("poi") or {}
        if not poi.get("poi_id") or not poi.get("name"):
            errors.append("poi_identity_missing")
        else:
            source = SourceRef.model_validate(poi.get("source") or {"title": poi["name"], "provider": "高德地图"})
            evidence.extend([
                EvidenceItem(id="attraction.name", field="attraction.name", value=poi["name"], source=source),
                EvidenceItem(id="attraction.location", field="attraction.location", value=poi.get("location") or "", source=source),
                EvidenceItem(id="attraction.address", field="attraction.address", value=poi.get("address") or "", source=source),
            ])
        if not result.get("nearby"):
            warnings.append("nearby_spots_empty")
    elif name == "search_amap":
        items = result.get("items") or []
        if not items:
            errors.append("search_returned_no_items")
        for index, poi in enumerate(items[:10]):
            source = SourceRef.model_validate(poi.get("source") or {"title": poi.get("name") or "高德地点", "provider": "高德地图"})
            evidence.append(EvidenceItem(id=f"poi.{index}.name", field=f"poi.{index}.name", value=poi.get("name") or "", source=source))
    elif name == "verify_official_page":
        matches = result.get("matches") or []
        if result.get("status") == "official" and matches:
            source = SourceRef(title=str(arguments.get("name") or "景区官网"), url=str(result.get("url") or ""), provider="官方网站", excerpt="；".join(matches)[:500])
            evidence.append(EvidenceItem(id="official.excerpt", field="official.excerpt", value=matches, source=source))
        else:
            warnings.append("official_information_unverified")
    elif name == "search_web":
        items = result.get("items") or []
        if not items:
            warnings.append("web_search_empty")
        for index, item in enumerate(items[:5]):
            source = SourceRef(title=str(item.get("title") or "搜索结果"), url=str(item.get("url") or ""), provider="公开搜索", excerpt="仅搜索结果，不抓取正文")
            evidence.append(EvidenceItem(id=f"web.{index}.title", field=f"web.{index}.title", value=item.get("title") or "", source=source))
    elif name == "get_weather":
        weather = result.get("weather") or {}
        casts = weather.get("casts") or []
        if not casts:
            warnings.append("weather_empty")
        else:
            source = SourceRef(title="高德天气", provider="高德地图", excerpt=f"报告时间：{weather.get('report_time') or '未知'}")
            evidence.append(EvidenceItem(id="weather.forecast", field="weather.forecast", value=casts, source=source, status="verified"))
    elif name == "query_train":
        periods = result.get("ticket_periods") or []
        if periods:
            if TRAIN_CODE_PATTERN.search(json.dumps(periods, ensure_ascii=False)):
                errors.append("train_code_leak")
            source = SourceRef(title="12306 余票时段", provider="12306", excerpt="仅聚合时段，不保留具体车次")
            evidence.append(EvidenceItem(id="transport.ticket_periods", field="transport.ticket_periods", value=periods, source=source))
    elif name == "query_flight":
        flights = result.get("flights") or []
        if not flights:
            warnings.append("flight_options_empty")
        else:
            first = flights[0]
            source = SourceRef.model_validate(first.get("source") or {
                "title": "Google Flights",
                "provider": "Google Flights / SerpAPI",
                "url": "https://www.google.com/travel/flights",
                "excerpt": "航班价格和总耗时",
            })
            sanitized_flights = [
                {key: value for key, value in item.items() if key != "flight_number"}
                for item in flights
            ]
            evidence.append(EvidenceItem(id="transport.flights", field="transport.flights", value=sanitized_flights, source=source))
    elif name == "route_estimate":
        route = result.get("route") or {}
        if not route.get("origin_location") or not route.get("destination_location"):
            errors.append("route_location_missing")
        elif not route.get("distance_m"):
            errors.append("route_distance_missing")
        else:
            source = SourceRef.model_validate(route.get("source") or {"title": "高德驾车路线", "provider": "高德地图"})
            evidence.append(EvidenceItem(id="transport.route", field="transport.route", value={"distance_m": route.get("distance_m"), "duration_s": route.get("duration_s")}, source=source))
    elif name == "get_preferences":
        evidence.append(EvidenceItem(id="preferences", field="preferences", value=result.get("preferences") or {}, status="verified"))

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, evidence=evidence)


def validate_final_result(final_tool: str, data: dict[str, Any]) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult(ok=False, errors=["final_result_not_object"])
    errors: list[str] = []
    warnings: list[str] = []
    if final_tool == "submit_attraction_page":
        if not str(data.get("summary") or "").strip():
            errors.append("summary_missing")
        if not isinstance(data.get("travel_tips"), list):
            errors.append("travel_tips_not_list")
    elif final_tool == "submit_transport_plan":
        if not str(data.get("blessing") or "").strip():
            errors.append("blessing_missing")
        if TRAIN_CODE_PATTERN.search(str(data)):
            errors.append("train_code_leak")
        recommendation = data.get("recommendation")
        if recommendation is not None:
            mode = recommendation.get("mode") if isinstance(recommendation, dict) else ""
            if mode not in {"drive", "high_speed_rail", "flight", "high_speed_rail_or_flight", "mixed"}:
                errors.append("transport_recommendation_invalid")
    else:
        errors.append(f"unknown_final_tool:{final_tool}")
    if data.get("ticket_price") and not data.get("official_excerpts"):
        warnings.append("ticket_price_without_official_excerpt")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
