from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import cache_compute_lock
from ..config import get_settings
from ..db import Repository
from ..mcp.client import LocalDataClient, McpDataClient, McpToolClient, McpToolRegistry
from ..observability import observation, observation_context, trace_value, update
from ..providers.amap import AmapClient, ProviderError
from ..providers.flights import SerpApiFlightProvider
from ..evaluation.vision import VisionUnavailable, assess_image_match, review_gallery
from ..providers.images import ImageCache
from ..providers.official import OfficialPageProvider
from ..providers.official_images import OfficialImageProvider
from ..providers.rail import Train12306Client
from ..providers.wikimedia import WikimediaImageProvider
from ..schemas import (
    AgentEvent,
    AttractionDetail,
    AttractionSummary,
    Blessing,
    FlightOption,
    FlightPeriod,
    GuideLink,
    ProvinceSummary,
    RouteMapInfo,
    SearchResponse,
    SourceRef,
    SubSpot,
    TicketPeriod,
    TransportComparison,
    TransportPlan,
    TransportRecommendation,
    WeatherAdvice,
)
from .ticket_periods import (
    build_flight_periods,
    build_itinerary_ticket_periods,
    build_rail_itinerary,
    summarize_rail_itinerary,
    summarize_trains,
)
from .deterministic import DeterministicRuntime
from .graph.runtime import LangGraphRuntime
from .state import build_replan_delta
from .tools import ToolRegistry


class TravelAgentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime: Any | None = None,
        amap: AmapClient | None = None,
        rail: Train12306Client | None = None,
        official: OfficialPageProvider | None = None,
        official_images: OfficialImageProvider | None = None,
        image_cache: ImageCache | None = None,
        wikimedia: WikimediaImageProvider | None = None,
        flights: SerpApiFlightProvider | None = None,
        mcp_client: McpToolClient | None = None,
    ):
        self.session = session
        self.repo = Repository(session)
        settings = get_settings()
        self.variant = settings.ablation_variant
        if runtime is not None:
            self.runtime = runtime
        elif self.variant == "no_llm":
            self.runtime = DeterministicRuntime()
        else:
            self.runtime = LangGraphRuntime()
        self.amap = amap or AmapClient()
        self.rail = rail or Train12306Client()
        self.official = official or OfficialPageProvider()
        self.official_images = official_images or OfficialImageProvider(self.official)
        self.images = image_cache or ImageCache()
        self.wikimedia = wikimedia or WikimediaImageProvider()
        self.flights = flights or SerpApiFlightProvider()
        self.mcp_client = mcp_client
        self.data = McpDataClient(mcp_client) if mcp_client else LocalDataClient(self.amap, self.rail, self.official, self.flights)

    async def _cache_get(self, kind: str, key: str, ttl_seconds: int) -> Any | None:
        if self.variant == "no_cache":
            return None
        return await self.repo.cache_get(kind, key, ttl_seconds)

    async def _cache_set(self, kind: str, key: str, value: Any, ttl_seconds: int = 86_400) -> None:
        if self.variant == "no_cache":
            return
        await self.repo.cache_set(kind, key, value, ttl_seconds)

    async def _tool_registry(
        self,
        tool_names: list[str],
        preferences: dict[str, Any],
        preloaded: dict[str, dict[str, Any]] | None = None,
    ) -> Any:
        if self.mcp_client:
            registry = McpToolRegistry(self.mcp_client, tool_names, preloaded=preloaded, preferences=preferences)
            await registry.initialize()
            return registry
        return ToolRegistry(amap=self.amap, rail=self.rail, official=self.official, preferences=preferences, preloaded=preloaded, flights=self.flights)

    async def provinces(self, *, _lock_held: bool = False) -> list[ProvinceSummary]:
        cached = await self._cache_get("provinces", f"{self.variant}:china", 86400)
        if cached is not None:
            return [ProvinceSummary.model_validate(item) for item in cached]
        if not _lock_held:
            async with cache_compute_lock("provinces", f"{self.variant}:china") as acquired:
                if acquired:
                    return await self.provinces(_lock_held=True)
        raw_provinces = await self.data.china_provinces()
        provinces = [item if isinstance(item, ProvinceSummary) else ProvinceSummary.model_validate(item) for item in raw_provinces]
        await self._cache_set("provinces", f"{self.variant}:china", [item.model_dump(mode="json") for item in provinces], 86400)
        return provinces

    async def province_attractions(
        self,
        adcode: str,
        province_name: str = "",
        *,
        style_preference: str = "balanced",
        _lock_held: bool = False,
    ) -> list[AttractionSummary]:
        style = style_preference if style_preference in {"landscape", "culture", "balanced"} else "balanced"
        cache_key = f"v6:{self.variant}:{style}:{adcode}:{province_name}"
        cached = await self._cache_get("province_attractions", cache_key, 86400)
        if cached is not None:
            return [AttractionSummary.model_validate(item) for item in cached]
        if not _lock_held:
            async with cache_compute_lock("province_attractions", cache_key) as acquired:
                if acquired:
                    return await self.province_attractions(
                        adcode,
                        province_name,
                        style_preference=style_preference,
                        _lock_held=True,
                    )
        search_terms = [f"{province_name} 5A景区", f"{province_name} 风景名胜", f"{province_name} 热门景点", f"{province_name} 博物馆"]
        excluded_name_parts = ("停车场", "售票", "游客中心", "入口", "出口", "地图", "城图", "服务中心", "公交站")
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for term in search_terms:
            if not term.strip():
                continue
            try:
                found = await self.data.search_pois(term, city=adcode, types="110000|140000", offset=25)
            except ProviderError:
                continue
            for poi in found:
                name = str(poi.get("name") or "")
                if "-" in name or any(part in name for part in excluded_name_parts):
                    continue
                if poi["poi_id"] and poi["poi_id"] not in seen:
                    seen.add(poi["poi_id"])
                    candidates.append(poi)
            if len(candidates) >= 30:
                break
        if not candidates:
            if province_name in {"香港特别行政区", "澳门特别行政区", "台湾省"}:
                return []
            raise ProviderError(f"高德没有返回 {province_name or adcode} 的景区候选")
        ranked_candidates = sorted(
            candidates,
            key=lambda item: (-float(item.get("rating") or 0), str(item.get("name") or "")),
        )[:30]
        chosen = await self._personalized_attraction_selection(ranked_candidates, province_name, style)
        reason = {
            "landscape": "根据你的山水偏好，结合高德评分与景区类型排序",
            "culture": "根据你的人文偏好，结合高德评分与景区类型排序",
            "balanced": "根据均衡偏好，结合高德评分与景区类型排序",
        }[style]
        selected = [self._summary(item, reason) for item in chosen[:7]]

        async def enrich_images(item: AttractionSummary) -> None:
            try:
                images = await self.wikimedia.search(item.name, item.city, limit=3, width=1800, expected_location=item.location)
            except Exception:
                images = []
            urls = [str(image.get("url") or "") for image in images if image.get("url")]
            item.photos = list(dict.fromkeys(urls + list(item.photos or [])))[:4]
        await asyncio.gather(*(enrich_images(item) for item in selected))
        await self._cache_set("province_attractions", cache_key, [item.model_dump(mode="json") for item in selected], 86400)
        return selected

    @staticmethod
    def _attraction_style(poi: dict[str, Any]) -> str:
        text = f"{poi.get('name') or ''} {poi.get('type') or ''} {poi.get('address') or ''}"
        landscape_keywords = ("山", "湖", "江", "河", "海", "瀑布", "森林", "峡谷", "岛", "湿地", "草原", "自然", "风景", "洞", "泉", "溪", "峰", "雪山", "沙滩", "地质", "公园")
        culture_keywords = ("博物", "古城", "古镇", "寺", "庙", "宫", "陵", "遗址", "纪念馆", "文化", "故居", "楼", "阁", "塔", "石窟", "城墙", "园", "祠", "书院", "街")
        landscape_score = sum(1 for keyword in landscape_keywords if keyword in text)
        culture_score = sum(1 for keyword in culture_keywords if keyword in text)
        if culture_score > landscape_score:
            return "culture"
        if landscape_score > culture_score:
            return "landscape"
        return "balanced"

    @classmethod
    def _fallback_personalized_selection(cls, candidates: list[dict[str, Any]], style: str, limit: int = 7) -> list[dict[str, Any]]:
        landscape = [item for item in candidates if cls._attraction_style(item) == "landscape"]
        culture = [item for item in candidates if cls._attraction_style(item) == "culture"]
        neutral = [item for item in candidates if cls._attraction_style(item) == "balanced"]
        if style == "landscape":
            primary, secondary = landscape + neutral, culture
            target_secondary = min(2, len(secondary))
        elif style == "culture":
            primary, secondary = culture + neutral, landscape
            target_secondary = min(2, len(secondary))
        else:
            primary, secondary = landscape + culture + neutral, []
            target_secondary = 0
        chosen = primary[: limit - target_secondary] + secondary[:target_secondary]
        if len(chosen) < limit:
            for item in candidates:
                if item not in chosen:
                    chosen.append(item)
                if len(chosen) >= limit:
                    break
        return chosen[:limit]

    async def _personalized_attraction_selection(
        self,
        candidates: list[dict[str, Any]],
        province_name: str,
        style: str,
    ) -> list[dict[str, Any]]:
        fallback = self._fallback_personalized_selection(candidates, style)
        client = getattr(self.runtime, "client", None)
        if client is None or len(candidates) <= 7:
            return fallback
        preview = [
            {
                "poi_id": item.get("poi_id"),
                "name": item.get("name"),
                "type": item.get("type"),
                "address": item.get("address"),
                "rating": item.get("rating"),
                "style": self._attraction_style(item),
            }
            for item in candidates[:24]
        ]
        style_text = {"landscape": "山水自然", "culture": "历史人文", "balanced": "山水与人文均衡"}[style]
        prompt = (
            f"你在为{province_name}挑选7个热门景区。用户偏好：{style_text}。"
            "只能从给定候选 poi_id 中选择，不能新增；高德评分只是参考。"
            "山水偏好时选择约5个山水和1-2个人文；人文偏好反之；均衡时约为4:3。"
            '只返回 JSON：{"poi_id": ["..."]}。候选：'
            + json.dumps(preview, ensure_ascii=False)
        )
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=get_settings().deepseek_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                ),
                timeout=20,
            )
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
            ids = [str(item) for item in (payload.get("poi_id") or []) if str(item)]
            by_id = {item.get("poi_id"): item for item in candidates}
            selected = [by_id[poi_id] for poi_id in ids if poi_id in by_id]
            selected = list({item.get("poi_id"): item for item in selected}.values())
            preferred = "landscape" if style == "landscape" else "culture" if style == "culture" else ""
            if preferred:
                preferred_count = sum(1 for item in selected if self._attraction_style(item) == preferred)
                secondary_count = sum(1 for item in selected if self._attraction_style(item) not in {preferred, "balanced"})
                if len(selected) < 7 or preferred_count < 4 or not (1 <= secondary_count <= 2):
                    return fallback
            elif len(selected) < 7:
                return fallback
            return selected[:7]
        except Exception:
            return fallback

    async def search(self, query: str, province: str = "", nationwide: bool = False) -> SearchResponse:
        query = query.strip()
        if not query:
            return SearchResponse(query=query)
        city = "" if nationwide else province
        pois = await self.data.search_pois(query, city=city, types="110000|140000", offset=25)
        results = [self._summary(item, "搜索结果") for item in pois[:5]]
        exact = next((item for item in results if item.name == query), None)
        direct = exact.poi_id if exact else (results[0].poi_id if len(results) == 1 else None)
        return SearchResponse(query=query, results=results, direct_poi_id=direct)

    async def attraction_stream(self, poi_id: str, trace_context: dict[str, str] | None = None) -> AsyncIterator[AgentEvent]:
        cache_key = f"v24:{self.variant}:{poi_id}"
        cached = await self._cache_get("attraction_detail", cache_key, 43200)
        if cached:
            yield AgentEvent(event="status", data="读取已缓存景区资料", step=0)
            yield AgentEvent(event="done", data=cached, step=0)
            return
        partial_cached = await self._cache_get("attraction_partial", cache_key, 43200)
        if partial_cached:
            yield AgentEvent(event="status", data="读取已缓存高德基础资料", step=0)
            yield AgentEvent(event="partial", data=partial_cached, step=0)
        preferences = await self.repo.get_preferences()
        tool_names = ["get_attraction_detail", "verify_official_page", "search_web", "get_weather"]
        registry = await self._tool_registry(tool_names, preferences)
        events: list[dict[str, Any]] = []
        captured: dict[str, Any] = {}
        web_results: list[dict[str, Any]] = []
        final: dict[str, Any] | None = None
        run_id = ""
        run_state: dict[str, Any] = {}
        async for event in self.runtime.run_stream(
            task="attraction_page",
            system_prompt=self.runtime.attraction_system_prompt(),
            user_prompt=f"请生成景区 POI ID 为 {poi_id} 的页面数据。",
            tools=registry,
            tool_names=tool_names,
            final_tool="submit_attraction_page",
            constraints={"poi_id": poi_id},
            trace_context=trace_context,
        ):
            run_id = event.run_id or run_id
            if event.meta.get("state"):
                run_state = event.meta["state"]
            events.append(event.model_dump(mode="json"))
            if event.event == "tool_result" and isinstance(event.data, dict):
                tool = event.data.get("tool")
                result = event.data.get("result") or {}
                if result.get("ok"):
                    captured[tool] = result
            if event.event == "error":
                yield event
                await self.repo.save_agent_run(run_id or uuid.uuid4().hex[:16], "attraction_page", "failed", events)
                return
            if event.event == "done":
                final = event.data if isinstance(event.data, dict) else {}
                break
            yield event
            if event.event == "tool_result" and isinstance(event.data, dict) and event.data.get("tool") == "get_attraction_detail":
                poi = (event.data.get("result") or {}).get("poi") or {}
                if poi.get("poi_id") and poi.get("name"):
                    partial_payload = self._partial_attraction_payload(poi)
                    await self._cache_set("attraction_partial", cache_key, partial_payload, 43200)
                    yield AgentEvent(event="partial", data=partial_payload, step=event.step, run_id=run_id)
        if final is None:
            return
        base = (captured.get("get_attraction_detail") or {}).get("poi")
        if not base:
            error = AgentEvent(event="error", data="Agent 未取得高德景区详情", run_id=run_id)
            await self.repo.save_agent_run(run_id or uuid.uuid4().hex[:16], "attraction_page", "failed", events)
            yield error
            return
        nearby = (captured.get("get_attraction_detail") or {}).get("nearby") or []
        official = captured.get("verify_official_page") or {}
        weather = (captured.get("get_weather") or {}).get("weather") or {}
        detail = await self._build_attraction_detail(base, nearby, official, weather, final, web_results, trace_context=trace_context)
        payload = detail.model_dump(mode="json")
        payload["evidence"] = run_state.get("evidence", [])
        payload["agent"] = self._agent_meta(run_state, run_id)
        await self._cache_set("attraction_detail", cache_key, payload, 43200)
        await self.repo.save_agent_run(run_id or uuid.uuid4().hex[:16], "attraction_page", "completed", events, payload)
        yield AgentEvent(event="done", data=payload, step=0, run_id=run_id)

    async def transport_stream(
        self,
        *,
        origin: str,
        destination: str,
        travel_date: date | None = None,
        poi_id: str = "",
        days: int | None = None,
        session_id: str = "",
        trace_context: dict[str, str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if not origin.strip():
            raise ProviderError("出发地必须输入")
        graph_thread_id = session_id or uuid.uuid4().hex[:16]
        planning_days = days or 3
        assumed_days = days is None
        try:
            weather = await self.data.weather(destination)
        except Exception:
            weather = {}
        route_info = await self._resolve_route(origin, destination, poi_id, {})
        planning_date = travel_date or self._choose_planning_date(weather)
        date_is_recommended = travel_date is None
        preferences = await self.repo.get_preferences()
        if origin:
            preferences["origin"] = origin
            await self.repo.save_preferences(preferences)
        current_constraints = {
            "origin": origin,
            "destination": destination,
            "travel_date": planning_date.isoformat(),
            "days": planning_days,
            "poi_id": poi_id,
        }
        previous_session = await self.repo.get_planning_session(session_id) if session_id else None
        replan_delta = build_replan_delta(
            "transport_plan",
            (previous_session or {}).get("constraints"),
            current_constraints,
        )
        preloaded: dict[str, dict[str, Any]] = {
            "route_estimate": {"ok": True, "route": route_info.model_dump(mode="json")},
            "get_weather": {"ok": True, "weather": weather},
        }
        if previous_session and previous_session.get("transport_plan"):
            previous_plan = previous_session["transport_plan"]
            if "route_estimate" not in preloaded and "transport.route" not in replan_delta.invalidated_evidence and previous_plan.get("route"):
                preloaded["route_estimate"] = {"ok": True, "route": previous_plan["route"]}
            if "get_weather" not in preloaded and "weather.forecast" not in replan_delta.invalidated_evidence and previous_plan.get("weather"):
                preloaded["get_weather"] = {"ok": True, "weather": previous_plan["weather"]}
            if "transport.ticket_periods" not in replan_delta.invalidated_evidence and previous_plan.get("ticket_periods"):
                preloaded["query_train"] = {
                    "ok": True,
                    "ticket_periods": previous_plan["ticket_periods"],
                    "train_summary": previous_plan.get("train_summary") or {},
                }
        cache_key = f"v5:{self.variant}:{origin}|{destination}|{planning_date.isoformat()}|{planning_days}|{poi_id}"
        cached = await self._cache_get("transport_plan", cache_key, 120)
        if cached:
            cached = {**cached, "cache_hit": True}
            yield AgentEvent(event="status", data="读取 2 分钟内实时交通缓存", run_id="cache")
            yield AgentEvent(event="done", data=cached, run_id="cache")
            return
        tool_names = ["get_preferences", "query_train", "query_flight", "route_estimate", "get_weather"]
        registry = await self._tool_registry(tool_names, preferences, preloaded=preloaded) if self.mcp_client else ToolRegistry(
            amap=self.amap,
            rail=self.rail,
            official=self.official,
            preferences=preferences,
            preloaded=preloaded,
        )
        captured: dict[str, Any] = {}
        train_results: list[dict[str, Any]] = []
        flight_results: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        final: dict[str, Any] | None = None
        run_id = ""
        run_state: dict[str, Any] = {}
        prompt = (
            f"出发地：{origin}\n目的地：{destination}\n计划出发日期：{planning_date.isoformat()}\n"
            f"旅行天数：{planning_days}{'（用户未填，按 3 天评估）' if assumed_days else ''}\n"
            f"驾车距离：{route_info.distance_m / 1000:.1f} 公里，预计 {route_info.duration_s / 3600:.1f} 小时\n"
            f"景区 POI：{poi_id}。"
            "必须调用 query_train。先查直达；直达无车次或查询失败时，不要停止，也不要限定中转次数。"
            "继续查询任意数量的铁路路段，并同时分析航班直达/中转、铁路接驳航班和航班接驳铁路。"
            "可以基于时间、费用、换乘次数、等待时间和大多数人的性价比偏好自主决定调用 query_flight。"
            "航班查询可使用最合适的实际机场或枢纽名称，可查询多个候选，但不得编造车次、航班、票价或时间。"
            "最终提交 recommendation、祝语和注意事项，不得编造车次、航班、票价或时间。"
        )
        async for event in self.runtime.run_stream(
            task="transport_plan",
            system_prompt=self.runtime.transport_system_prompt(),
            user_prompt=prompt,
            tools=registry,
            tool_names=tool_names,
            final_tool="submit_transport_plan",
            constraints={
                "origin": origin,
                "destination": destination,
                "travel_date": planning_date.isoformat(),
                "days": planning_days,
                "poi_id": poi_id,
                "thread_id": graph_thread_id,
            },
            trace_context=trace_context,
        ):
            run_id = event.run_id or run_id
            if event.meta.get("state"):
                run_state = event.meta["state"]
            events.append(event.model_dump(mode="json"))
            if event.event == "tool_result" and isinstance(event.data, dict):
                tool = event.data.get("tool")
                result = event.data.get("result") or {}
                if result.get("ok"):
                    captured[tool] = result
                    if tool == "query_train":
                        train_results.append({
                            "arguments": event.data.get("arguments") or {"origin": origin, "destination": destination},
                            "result": result,
                        })
                    if tool == "query_flight":
                        flight_results.extend(result.get("flights") or [])
                    if tool == "search_web":
                        web_results.extend(result.get("items") or [])
            if event.event == "done":
                final = event.data if isinstance(event.data, dict) else {}
                break
            yield event
            if event.event == "error":
                return
        if final is None:
            return
        if not train_results:
            try:
                train_payload = await self.data.query_train_payload(origin, destination, planning_date.isoformat())
                train_results.append({
                    "arguments": {"origin": origin, "destination": destination, "travel_date": planning_date.isoformat()},
                    "result": train_payload,
                })
            except Exception:
                train_results = []
        itinerary = build_rail_itinerary(train_results, origin, destination)
        periods = build_itinerary_ticket_periods(itinerary)
        train_summary = summarize_rail_itinerary(itinerary)
        train_summary["direct_available"] = bool(itinerary.get("direct"))
        rail_minutes = int(train_summary.get("min_duration_minutes") or 0)
        rail_available = bool(train_summary.get("train_count")) and rail_minutes > 0
        direct_rail_available = bool(itinerary.get("direct"))
        flight_comparison_allowed = not rail_available or rail_minutes > 360 or not direct_rail_available
        requested_mode = str((final.get("recommendation") or {}).get("mode") or "") if isinstance(final.get("recommendation"), dict) else ""
        flight_required = requested_mode in {"flight", "high_speed_rail_or_flight"} and flight_comparison_allowed
        flight_options: list[FlightOption] = []
        seen_flights: set[tuple[str, str, str, str]] = set()
        for item in flight_results:
            try:
                option = FlightOption.model_validate(item)
            except Exception:
                continue
            key = (option.flight_number, option.departure_time, option.departure_airport, option.arrival_airport)
            if key in seen_flights:
                continue
            seen_flights.add(key)
            flight_options.append(option)
        if not flight_comparison_allowed:
            flight_options = []
        if flight_required and not flight_options:
            try:
                flight_options = [
                    FlightOption.model_validate(item)
                    for item in await self.data.query_flights(
                        route_info.origin_city or origin,
                        route_info.destination_city or destination,
                        planning_date.isoformat(),
                    )
                ]
            except Exception:
                flight_options = []
        recommendation = self._recommend_transport(
            final.get("recommendation") if isinstance(final.get("recommendation"), dict) else {},
            route_info,
            train_summary,
            flight_options,
        )
        flight_periods = [FlightPeriod.model_validate(item) for item in build_flight_periods(flight_options)]
        comparison = self._transport_comparison(train_summary, flight_options)
        weather_advice = self._weather_advice(weather, planning_date, planning_days, date_is_recommended)
        plan = self._build_transport_plan(
            origin,
            destination,
            planning_date,
            final,
            route_info,
            periods,
            weather,
            planning_days,
            recommendation=recommendation,
            flight_options=flight_options,
            flight_periods=flight_periods,
            comparison=comparison,
            weather_advice=weather_advice,
            date_is_recommended=date_is_recommended,
            assumed_days=assumed_days,
            train_summary=train_summary,
        )
        payload = plan.model_dump(mode="json")
        payload["train_summary"] = train_summary
        payload["flight_comparison_allowed"] = flight_comparison_allowed
        payload["rail_itinerary"] = {
            "available": bool(itinerary.get("available")),
            "total_minutes": int(itinerary.get("total_minutes") or 0),
            "segments": [
                {
                    "origin": segment.get("origin") or "",
                    "destination": segment.get("destination") or "",
                    "minutes": int(segment.get("minutes") or 0),
                }
                for segment in itinerary.get("segments") or []
            ],
        }
        payload["evidence"] = run_state.get("evidence", [])
        payload["agent"] = self._agent_meta(run_state, run_id)
        session_id = graph_thread_id
        payload["session_id"] = session_id
        payload["cache_hit"] = False
        payload["replan"] = replan_delta.model_dump(mode="json")
        payload["preloaded_tools"] = sorted(preloaded)
        await self._cache_set("transport_plan", cache_key, payload, 120)
        await self.repo.save_session(session_id, {
            "id": session_id,
            "origin": origin,
            "selected_poi_id": poi_id,
            "constraints": current_constraints,
            "transport_plan": payload,
            "updated_at": datetime.now().isoformat(),
        })
        await self.repo.save_agent_run(run_id or session_id, "transport_plan", "completed", events, payload)
        yield AgentEvent(event="done", data=payload, step=0, run_id=run_id or session_id)

    def guide_links(self, name: str) -> list[GuideLink]:
        query = quote_plus(name)
        return [
            GuideLink(platform="小红书", url=f"https://www.xiaohongshu.com/search_result?keyword={query}"),
            GuideLink(platform="知乎", url=f"https://www.zhihu.com/search?type=content&q={query}"),
        ]

    async def _best_spot_image(
        self,
        name: str,
        city: str,
        fallback_urls: list[str],
        *,
        fallback_first: bool = False,
    ) -> str:
        try:
            images = await self.wikimedia.search(name, city, limit=3, width=2000, expected_location="")
        except Exception:
            images = []
        wikimedia_urls = [str(item.get("url") or "") for item in images if item.get("url")]
        fallbacks = [url for url in fallback_urls if url]
        candidates = fallbacks + wikimedia_urls if fallback_first else wikimedia_urls + fallbacks
        for candidate in list(dict.fromkeys(candidates)):
            cached = await self.images.cache(candidate)
            if cached:
                return cached
        return ""

    @staticmethod
    def _normalize_place_name(value: str) -> str:
        return "".join(char for char in value.lower() if char.isalnum())

    @classmethod
    def _match_nearby(cls, name: str, nearby_by_name: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        key = cls._normalize_place_name(name)
        if not key:
            return None
        exact = nearby_by_name.get(key)
        if exact:
            return exact
        for candidate_key, item in nearby_by_name.items():
            if key in candidate_key or candidate_key in key:
                return item
        return None

    @classmethod
    def _ranked_generic_image_urls(cls, images: list[dict[str, Any]], place_name: str) -> list[str]:
        negative_terms = (
            "night", "nacht", "street", "straße", "bird", "animal", "food",
            "carrier", "crowd", "sign", "logo", "bamboo", "panoramio",
        )
        place_key = cls._normalize_place_name(place_name)
        scored: list[tuple[float, int, str]] = []
        for index, item in enumerate(images):
            url = str(item.get("url") or "")
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            if not url or width < 1200 or height < 700:
                continue
            ratio = width / max(height, 1)
            if ratio < 1.2:
                continue
            title = str(item.get("title") or "").lower()
            if any(term in title and term not in place_key for term in negative_terms):
                continue
            score = float(width * height)
            if 1.25 <= ratio <= 2.5:
                score *= 1.2
            scored.append((score, -index, url))
        return [url for _, _, url in sorted(scored, reverse=True)]

    @classmethod
    def _hero_image_url(
        cls,
        images: list[dict[str, Any]],
        fallback_urls: list[str],
        place_name: str,
    ) -> str:
        ranked = cls._ranked_generic_image_urls(images, place_name)
        if ranked:
            return ranked[0]
        for item in images:
            if item.get("url"):
                return str(item["url"])
        return next((url for url in fallback_urls if url), "")

    async def _cache_landscape_image(self, urls: list[str]) -> str:
        for url in list(dict.fromkeys(url for url in urls if url)):
            cached = await self.images.cache(url)
            if not cached:
                continue
            try:
                image_path = self.images.directory / cached.rsplit("/", 1)[-1]
                from PIL import Image

                with Image.open(image_path) as image:
                    width, height = image.size
                if width >= 1600 and height >= 900 and width / max(height, 1) >= 1.2:
                    return cached
            except Exception:
                return cached
        return ""

    async def _cache_gallery_images(
        self,
        urls: list[str],
        *,
        exclude: str = "",
        limit: int = 6,
    ) -> list[str]:
        selected: list[str] = []
        for url in dict.fromkeys(url for url in urls if url):
            cached = await self.images.cache(url)
            if not cached or cached == exclude or cached in selected:
                continue
            try:
                image_path = self.images.directory / cached.rsplit("/", 1)[-1]
                from PIL import Image

                with Image.open(image_path) as image:
                    width, height = image.size
            except Exception:
                continue
            ratio = width / max(height, 1) if width and height else 0
            if width < 1000 or height < 600 or ratio < 1.1:
                continue
            selected.append(cached)
            if len(selected) >= limit:
                break
        return selected

    async def _cache_best_exact_image(self, urls: list[str]) -> tuple[str, tuple[int, int] | None]:
        ranked: list[tuple[float, str, tuple[int, int] | None]] = []
        for url in dict.fromkeys(url for url in urls if url):
            cached = await self.images.cache(url)
            if not cached:
                continue
            size: tuple[int, int] | None = None
            try:
                image_path = self.images.directory / cached.rsplit("/", 1)[-1]
                from PIL import Image

                with Image.open(image_path) as image:
                    size = image.size
            except Exception:
                pass
            width, height = size or (0, 0)
            ratio = width / max(height, 1) if width and height else 0
            quality = float(width * height)
            if 1.2 <= ratio <= 2.5 and width >= 1000 and height >= 700:
                quality += 5_000_000
            ranked.append((quality, cached, size))
        if not ranked:
            return "", None
        _, cached, size = max(ranked)
        return cached, size

    @staticmethod
    def _image_cache_path(url: str) -> str:
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg"
        return f"/media/{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}{suffix}"

    @classmethod
    def _image_cache_aliases(cls, url: str) -> tuple[str, str]:
        cached = cls._image_cache_path(url)
        path = Path(cached)
        return cached, str(path.with_name(f"{path.stem}-crop{path.suffix}"))

    async def _select_visual_gallery(
        self,
        name: str,
        city: str,
        preferred_urls: list[str],
        fallback_urls: list[str],
        *,
        exclude: str = "",
        limit: int = 6,
        trace_context: dict[str, str] | None = None,
    ) -> list[str]:
        preferred_set = set(preferred_urls)
        urls = list(dict.fromkeys([*preferred_urls, *fallback_urls]))[:20]
        download_semaphore = asyncio.Semaphore(6)

        async def load_candidate(url: str) -> tuple[int, int, str, bytes, bool] | None:
            async with download_semaphore:
                cached = await self.images.cache(url)
            if not cached or cached == exclude:
                return None
            image_path = self.images.directory / cached.rsplit("/", 1)[-1]
            try:
                from PIL import Image

                with Image.open(image_path) as image:
                    width, height = image.size
                if width < 320 or height < 200 or width / max(height, 1) < 1.1:
                    cropped = await asyncio.to_thread(self.images.center_crop, cached, 320, 200)
                    if not cropped:
                        return None
                    cached = cropped
                    image_path = self.images.directory / cached.rsplit("/", 1)[-1]
                    with Image.open(image_path) as image:
                        width, height = image.size
                    if width < 320 or height < 200 or width / max(height, 1) < 1.1:
                        return None
                return int(width * height), width, cached, image_path.read_bytes(), url in preferred_set
            except Exception:
                return None

        loaded = await asyncio.gather(*(load_candidate(url) for url in urls))
        rows: list[tuple[int, int, str, bytes, bool]] = []
        seen_hashes: set[str] = set()
        for row in loaded:
            if row is None:
                continue
            digest = hashlib.sha256(row[3]).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            rows.append(row)

        semaphore = asyncio.Semaphore(3)

        async def score(item: tuple[int, int, str, bytes, bool]) -> tuple[int, int, int, str, bytes]:
            area, _width, cached, payload, preferred = item
            if self.variant == "no_vision":
                return (85 if area >= 1_000_000 else 72), area, int(preferred) * 5, cached, payload
            try:
                async with semaphore:
                    with observation(
                        name="vision:image_match",
                        as_type="generation",
                        input={"name": name, "city": city, "image": cached},
                        model=get_settings().vision_model,
                        metadata={"area": area},
                        trace_context=trace_context,
                    ) as vision_span:
                        result = await assess_image_match(name, city, payload, "image/jpeg")
                        update(vision_span, output=trace_value(result))
                return int(result.get("score") or 0), area, int(preferred) * 5, cached, payload
            except Exception:
                return (65 if area >= 1_000_000 else 45), area, int(preferred) * 5, cached, payload

        ranked = await asyncio.gather(*(score(item) for item in rows))
        eligible = [item for item in ranked if item[0] >= 30]
        shortlist = sorted(eligible, key=lambda item: (item[0] + item[2], item[0], item[1]), reverse=True)[: max(limit * 2, 8)]
        if len(shortlist) >= 2 and self.variant != "no_vision":
            try:
                reviewed = await asyncio.wait_for(
                    review_gallery(name, city, [(payload, "image/jpeg") for _, _, _, _, payload in shortlist]),
                    timeout=20,
                )
                keep = []
                for index in reviewed.get("keep") or []:
                    value = int(index)
                    if 0 <= value < len(shortlist) and value not in keep:
                        keep.append(value)
                if keep:
                    duplicate_indices = {
                        int(index)
                        for group in reviewed.get("duplicate_groups") or []
                        if isinstance(group, list)
                        for index in group[1:]
                    }
                    ordered = [shortlist[index] for index in keep]
                    for index, item in enumerate(shortlist):
                        if len(ordered) >= limit:
                            break
                        if index not in keep and index not in duplicate_indices:
                            ordered.append(item)
                    shortlist = ordered[:limit]
            except Exception:
                pass
        return [item[3] for item in shortlist[:limit]]

    def _partial_attraction_payload(self, base: dict[str, Any]) -> dict[str, Any]:
        name = str(base.get("name") or "景区")
        photos = [
            (
                f"{url}?type=pic"
                if "store.is.autonavi.com/showpic/" in url and "?" not in url
                else url
            ).replace("http://", "https://", 1)
            for url in (base.get("photos") or [])
            if url
        ]
        try:
            reference_cost = float(base.get("cost") or 0)
        except (TypeError, ValueError):
            reference_cost = 0
        try:
            rating = float(base.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        return {
            "poi_id": str(base.get("poi_id") or ""),
            "name": name,
            "province": str(base.get("province") or ""),
            "city": str(base.get("city") or ""),
            "district": str(base.get("district") or ""),
            "address": str(base.get("address") or ""),
            "location": str(base.get("location") or ""),
            "rating": rating,
            "reference_cost": reference_cost,
            "ticket_price": "",
            "open_time": str(base.get("open_time") or ""),
            "telephone": str(base.get("tel") or ""),
            "open_status": "pending",
            "official_url": "",
            "official_excerpts": [],
            "summary": "高德基础资料已加载，官网核验、天气与景区图片正在补充。",
            "travel_tips": ["官网开放时间、票价和预约规则正在核验。"],
            "hero_image": photos[0] if photos else "",
            "photos": photos[:6],
            "image_sources": [],
            "sub_spots": [],
            "sources": [base["source"]] if base.get("source") else [],
            "guide_links": [item.model_dump(mode="json") for item in self.guide_links(name)],
            "weather": {},
            "generated_at": datetime.now().isoformat(),
        }

    async def _build_attraction_detail(
        self,
        base: dict[str, Any],
        nearby: list[dict[str, Any]],
        official: dict[str, Any],
        weather: dict[str, Any],
        final: dict[str, Any],
        web_results: list[dict[str, Any]],
        trace_context: dict[str, str] | None = None,
    ) -> AttractionDetail:
        base_photo_urls = [
            (f"{url}?type=pic" if "store.is.autonavi.com/showpic/" in url and "?" not in url else url)
            for url in (base.get("photos") or [])
        ]
        nearby_photo_urls: list[str] = []
        base_key = self._normalize_place_name(str(base.get("name") or ""))
        for item in nearby:
            item_key = self._normalize_place_name(str(item.get("name") or ""))
            related = bool(base_key and item_key and (base_key in item_key or item_key in base_key))
            if related:
                nearby_photo_urls.extend(
                    (f"{url}?type=pic" if "store.is.autonavi.com/showpic/" in url and "?" not in url else url)
                    for url in (item.get("photos") or [])
                    if url
                )
        photo_urls = [*base_photo_urls, *nearby_photo_urls]
        async def official_search() -> list[dict[str, Any]]:
            with observation(
                name="image:official_search",
                as_type="span",
                input={"name": str(base.get("name") or ""), "city": str(base.get("city") or ""), "official_url": str(base.get("website") or "")},
                trace_context=trace_context,
            ) as span:
                result = await self.official_images.find_for_attraction(
                    str(base.get("name") or ""),
                    str(base.get("city") or ""),
                    str(base.get("website") or ""),
                    limit=10,
                    page_limit=4,
                )
                update(span, output={"count": len(result), "urls": [item.get("url") for item in result[:10]]})
                return result

        async def wikimedia_search() -> list[dict[str, Any]]:
            with observation(
                name="image:wikimedia_search",
                as_type="span",
                input={"name": str(base.get("name") or ""), "city": str(base.get("city") or "")},
                trace_context=trace_context,
            ) as span:
                result = await self.wikimedia.search(
                    str(base.get("name") or ""),
                    str(base.get("city") or ""),
                    limit=30,
                    width=3000,
                    expected_location=str(base.get("location") or ""),
                    allow_generic=True,
                )
                update(span, output={"count": len(result)})
                return result

        try:
            official_images, wikimedia_images = await asyncio.gather(official_search(), wikimedia_search())
        except Exception:
            official_images = []
            try:
                wikimedia_images = await self.wikimedia.search(
                    str(base.get("name") or ""),
                    str(base.get("city") or ""),
                    limit=30,
                    width=3000,
                    expected_location=str(base.get("location") or ""),
                    allow_generic=True,
                )
            except Exception:
                wikimedia_images = []
        official_image_urls = [str(item.get("url") or "") for item in official_images if item.get("url")]
        image_candidates = [*official_image_urls]
        for item in wikimedia_images:
            if item.get("url"):
                image_candidates.append(str(item["url"]))
        image_candidates.extend(photo_urls)
        if final.get("hero_image"):
            image_candidates.append(str(final["hero_image"]))
        deduped_images = list(dict.fromkeys(image_candidates))
        hero_url = self._hero_image_url(wikimedia_images, [*official_image_urls, *photo_urls], str(base.get("name") or ""))
        hero_candidates = [hero_url, *official_image_urls, *photo_urls, *image_candidates]
        cached_photos, hero = await asyncio.gather(
            self.images.cache_many(deduped_images, limit=16),
            self._cache_landscape_image(hero_candidates),
        )
        if not hero:
            hero = await self.images.cache(hero_url) if hero_url else (cached_photos[0] if cached_photos else "")

        wikimedia_urls = self._ranked_generic_image_urls(wikimedia_images, str(base.get("name") or ""))
        preferred_urls = [*official_image_urls, *photo_urls]
        with observation(
            name="image:gallery_select",
            as_type="span",
            input={"name": str(base.get("name") or ""), "city": str(base.get("city") or "")},
            metadata={"preferred_candidates": len(preferred_urls), "fallback_candidates": len(wikimedia_urls)},
            trace_context=trace_context,
        ) as gallery_span:
            gallery = await self._select_visual_gallery(
                str(base.get("name") or ""),
                str(base.get("city") or ""),
                preferred_urls,
                wikimedia_urls,
                exclude=hero,
                limit=6,
                trace_context=observation_context(gallery_span) or trace_context,
            )
            update(gallery_span, output={"count": len(gallery), "images": gallery})
        source_lookup: dict[str, dict[str, str]] = {}
        for item in official_images:
            url = str(item.get("url") or "")
            if url:
                for image_path in self._image_cache_aliases(url):
                    source_lookup.setdefault(image_path, {
                        "image": image_path,
                        "provider": "官方文旅/景区网站",
                        "source_url": str(item.get("page_url") or ""),
                    })
        for url in photo_urls:
            for image_path in self._image_cache_aliases(url):
                source_lookup.setdefault(image_path, {
                    "image": image_path,
                    "provider": "高德地图",
                    "source_url": "https://www.amap.com/",
                })
        for item in wikimedia_images:
            url = str(item.get("url") or "")
            if url:
                for image_path in self._image_cache_aliases(url):
                    source_lookup.setdefault(image_path, {
                        "image": image_path,
                        "provider": "Wikimedia Commons",
                        "source_url": str(item.get("page_url") or ""),
                    })
        image_sources = [
            source_lookup.get(image, {"image": image, "provider": "unknown", "source_url": ""})
            for image in gallery
        ]

        official_status = "official" if official.get("status") == "official" and official.get("matches") else "amap_only"
        sources = [SourceRef.model_validate(base["source"])]
        if official.get("url") and official.get("matches"):
            sources.append(SourceRef(
                title=f"{base.get('name')} 官网",
                url=str(official.get("url") or ""),
                provider="官方网站",
                excerpt="；".join(official.get("matches") or [])[:500],
            ))
        if official_images:
            first_official = official_images[0]
            sources.append(SourceRef(
                title=f"{base.get('name')} 官方页面图片",
                url=str(first_official.get("page_url") or ""),
                provider="官方文旅/景区网站",
                excerpt="仅从公开官方文旅或景区页面提取代表性图片，未绕过登录或验证码",
            ))
        if wikimedia_images:
            first_image = wikimedia_images[0]
            sources.append(SourceRef(
                title=f"{base.get('name')} 高分辨率图片",
                url=str(first_image.get("page_url") or ""),
                provider="Wikimedia Commons",
                excerpt=f"{first_image.get('width')}×{first_image.get('height')} 公共图片",
            ))
        seen_urls = {source.url for source in sources}
        for item in web_results[:5]:
            url = str(item.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(SourceRef(
                title=str(item.get("title") or "搜索结果"),
                url=url,
                provider="公开搜索",
                excerpt="用于定位公开信息，页面正文未抓取",
            ))
        return AttractionDetail(
            poi_id=base["poi_id"],
            name=base["name"],
            province=base.get("province") or "",
            city=base.get("city") or "",
            district=base.get("district") or "",
            address=base.get("address") or "",
            location=base.get("location") or "",
            rating=float(base.get("rating") or 0),
            reference_cost=float(base.get("cost") or 0),
            ticket_price=str(final.get("ticket_price") or ""),
            open_time=str(final.get("open_time") or base.get("open_time") or ""),
            telephone=base.get("tel") or "",
            open_status=official_status,
            official_url=str(final.get("official_url") or official.get("url") or base.get("website") or ""),
            official_excerpts=[str(item) for item in (final.get("official_excerpts") or official.get("matches") or [])][:20],
            summary=str(final.get("summary") or f"{base.get('name')}是高德地图收录的真实景区地点。"),
            travel_tips=[str(item) for item in (final.get("travel_tips") or [])][:8],
            hero_image=hero or (cached_photos[0] if cached_photos else ""),
            photos=gallery,
            image_sources=image_sources,
            sub_spots=[],
            sources=sources,
            guide_links=self.guide_links(base["name"]),
            weather=weather,
        )

    @staticmethod
    def _agent_meta(state: dict[str, Any], run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "status": state.get("status") or "completed",
            "tool_calls": state.get("tool_calls", 0),
            "replan_count": state.get("replan_count", 0),
            "missing_fields": state.get("missing_fields", []),
            "token_usage": state.get("token_usage", {}),
            "warnings": state.get("warnings", []),
            "errors": state.get("errors", []),
        }

    async def _resolve_route(
        self,
        origin: str,
        destination: str,
        poi_id: str,
        captured: dict[str, Any],
    ) -> RouteMapInfo:
        route = ((captured.get("route_estimate") or {}).get("route") or {})
        origin_info: dict[str, Any] = {}
        destination_info: dict[str, Any] = {}
        origin_location = str(route.get("origin_location") or "")
        destination_location = str(route.get("destination_location") or "")
        try:
            origin_info = await self.data.geocode(origin)
        except Exception:
            origin_info = {}
        try:
            destination_info = await self.data.geocode(destination)
        except Exception:
            destination_info = {}
        if not origin_location:
            origin_location = str(origin_info.get("location") or "")
        if not destination_location and poi_id:
            try:
                destination_location = (await self.data.poi_detail(poi_id)).get("location") or ""
            except Exception:
                destination_location = ""
        if not destination_location:
            destination_location = str(destination_info.get("location") or "")
        if not route and origin_location and destination_location:
            route = await self.data.driving_route(origin_location, destination_location)
        if not origin_location or not destination_location:
            raise ProviderError("无法计算出发地到目的地的地图距离")
        def display_name(value: Any, info: dict[str, Any], fallback: str) -> str:
            text = str(value or "").strip()
            if text and not re.fullmatch(r"-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?", text):
                return text
            return str(info.get("formatted_address") or info.get("city") or info.get("province") or fallback)

        return RouteMapInfo(
            origin_name=display_name(route.get("origin_name"), origin_info, origin),
            destination_name=display_name(route.get("destination_name"), destination_info, destination),
            origin_location=origin_location,
            destination_location=destination_location,
            origin_province=str(route.get("origin_province") or origin_info.get("province") or ""),
            origin_city=str(route.get("origin_city") or origin_info.get("city") or ""),
            origin_district=str(route.get("origin_district") or origin_info.get("district") or ""),
            destination_province=str(route.get("destination_province") or destination_info.get("province") or ""),
            destination_city=str(route.get("destination_city") or destination_info.get("city") or ""),
            destination_district=str(route.get("destination_district") or destination_info.get("district") or ""),
            distance_m=int(route.get("distance_m") or 0),
            duration_s=int(route.get("duration_s") or 0),
            tolls=float(route.get("tolls") or 0),
            path=route.get("path") or [],
            source=SourceRef.model_validate(route.get("source") or {
                "title": "高德驾车路线",
                "provider": "高德地图",
                "url": "https://www.amap.com/",
                "excerpt": "驾车距离与时间",
            }),
        )

    @staticmethod
    def _bad_weather(weather: str) -> bool:
        return any(term in weather for term in ("雨", "雪", "雷", "暴雨"))

    @classmethod
    def _choose_planning_date(cls, weather: dict[str, Any]) -> date:
        casts = weather.get("casts") or []
        parsed: list[tuple[date, str]] = []
        for cast in casts:
            try:
                parsed.append((date.fromisoformat(str(cast.get("date") or "")), str(cast.get("dayweather") or "")))
            except ValueError:
                continue
        for day, condition in parsed:
            if day >= date.today() and not cls._bad_weather(condition):
                return day
        if parsed:
            return parsed[0][0]
        return date.today() + timedelta(days=1)

    @classmethod
    def _weather_advice(
        cls,
        weather: dict[str, Any],
        planning_date: date,
        days: int,
        date_is_recommended: bool,
    ) -> WeatherAdvice:
        casts = weather.get("casts") or []
        parsed: list[tuple[date, str]] = []
        for cast in casts:
            try:
                parsed.append((date.fromisoformat(str(cast.get("date") or "")), str(cast.get("dayweather") or "")))
            except ValueError:
                continue
        selected = [(day, condition) for day, condition in parsed if planning_date <= day < planning_date + timedelta(days=days)]
        bad_days = [(day, condition) for day, condition in selected if cls._bad_weather(condition)]
        suggested = [day.isoformat() for day, condition in parsed if not cls._bad_weather(condition)]
        if not bad_days:
            summary = "当前计划日期的天气较适合出行，出发前再确认一次即可。" if date_is_recommended else ""
            return WeatherAdvice(summary=summary, suggested_dates=suggested[:3], best_date=suggested[0] if suggested else "")
        best = suggested[0] if suggested else ""
        summary = (
            f"当前计划中有 {len(bad_days)} 天有雨雪，建议优先考虑 { '、'.join(suggested[:3]) } 等日期。"
            if suggested else "未来几天天气以雨雪为主，建议临近出发再确认，必要时调整日期。"
        )
        return WeatherAdvice(summary=summary, suggested_dates=suggested[:3], best_date=best)

    @staticmethod
    def _transport_comparison(train_summary: dict[str, Any], flight_options: list[FlightOption]) -> TransportComparison:
        train_price = float(train_summary.get("min_price") or 0)
        train_minutes = int(train_summary.get("min_duration_minutes") or 0)
        flight_price = min((item.price for item in flight_options if item.price > 0), default=0)
        flight_minutes = min((item.duration_minutes for item in flight_options if item.duration_minutes > 0), default=0)
        return TransportComparison(
            train_price=train_price,
            flight_price=flight_price,
            train_duration_minutes=train_minutes,
            flight_duration_minutes=flight_minutes,
            price_gap=round(abs(flight_price - train_price), 1) if flight_price and train_price else 0,
            duration_gap_minutes=abs(flight_minutes - train_minutes) if flight_minutes and train_minutes else 0,
            flight_available=bool(flight_options),
        )

    @staticmethod
    def _recommend_transport(
        llm: dict[str, Any],
        route: RouteMapInfo,
        train_summary: dict[str, Any],
        flight_options: list[FlightOption],
    ) -> TransportRecommendation:
        drive_minutes = int(route.duration_s / 60) if route.duration_s else 0
        rail_minutes = int(train_summary.get("min_duration_minutes") or 0)
        rail_available = bool(train_summary.get("train_count")) and rail_minutes > 0
        direct_rail_available = bool(train_summary.get("direct_available"))
        flight_available = bool(flight_options)
        long_rail = rail_available and rail_minutes > 360
        flight_comparison_allowed = long_rail or not direct_rail_available
        if flight_available and flight_comparison_allowed:
            mode = "high_speed_rail_or_flight" if rail_available else "flight"
        elif long_rail:
            mode = "flight" if flight_available else "high_speed_rail"
        elif not rail_available:
            mode = "drive" if drive_minutes else "mixed"
        elif drive_minutes and drive_minutes <= 180:
            mode = "high_speed_rail" if rail_available else "drive"
        else:
            mode = "high_speed_rail" if rail_available else "mixed"
        candidate = str(llm.get("mode") or "")
        allowed = {"drive", "high_speed_rail", "flight", "high_speed_rail_or_flight", "mixed"}
        invalid = candidate not in allowed
        if candidate == "drive" and not drive_minutes:
            invalid = True
        if candidate == "flight" and not flight_available:
            invalid = True
        if candidate in {"flight", "high_speed_rail_or_flight"} and not flight_comparison_allowed:
            invalid = True
        if candidate == "high_speed_rail" and not rail_available:
            invalid = True
        if candidate == "high_speed_rail_or_flight" and not (rail_available and flight_available):
            invalid = True
        if not invalid:
            return TransportRecommendation(
                mode=candidate,
                summary=str(llm.get("summary") or ""),
                reason=str(llm.get("reason") or ""),
            )
        if mode == "drive":
            summary, reason = "建议优先驾车出行。", "驾车时间较短，适合灵活安排。"
        elif mode == "flight":
            summary, reason = "建议优先考虑飞机。", "高铁耗时较长或没有合适直达方案。"
        elif mode == "high_speed_rail_or_flight":
            summary, reason = "建议比较高铁和飞机，优先选择更省时或更经济的方案。", "高铁耗时超过 6 小时，飞机可能更有优势。"
        else:
            summary, reason = "建议优先考虑高铁。", "高铁稳定、准点，综合时间更可控。"
        return TransportRecommendation(mode=mode, summary=summary, reason=reason)

    def _build_transport_plan(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        final: dict[str, Any],
        route: RouteMapInfo,
        ticket_periods: list[TicketPeriod],
        weather: dict[str, Any],
        days: int = 1,
        *,
        recommendation: TransportRecommendation | None = None,
        flight_options: list[FlightOption] | None = None,
        flight_periods: list[FlightPeriod] | None = None,
        comparison: TransportComparison | None = None,
        weather_advice: WeatherAdvice | None = None,
        date_is_recommended: bool = False,
        assumed_days: bool = False,
        train_summary: dict[str, Any] | None = None,
    ) -> TransportPlan:
        blessing_text = str(final.get("blessing") or "").strip() or (
            f"山高水远路迢迢，{destination}风光真叫妙；"
            "身份证和雨伞都带好，出门在外少烦恼。"
        )
        caveats = [str(item) for item in (final.get("caveats") or [])]
        if not ticket_periods:
            caveats.append("当天并无余票，可尝试调整日期或选择其他交通方式。")
        resolved_recommendation = recommendation or TransportRecommendation(summary="请结合价格和时间选择交通方式。")
        rail_summary = train_summary or {}
        rail_available = bool(rail_summary.get("train_count")) and int(rail_summary.get("min_duration_minutes") or 0) <= 360
        show_driving_plan = resolved_recommendation.mode not in {"flight", "high_speed_rail_or_flight"} and bool(route.distance_m)
        if not flight_periods and resolved_recommendation.mode in {"flight", "high_speed_rail_or_flight"}:
            caveats.append("当天暂未查到有余票的航班。")
        return TransportPlan(
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            route=route,
            ticket_periods=ticket_periods,
            blessing=Blessing(text=blessing_text, style="lively"),
            days=max(1, min(int(days or 1), 14)),
            weather=weather,
            caveats=caveats,
            recommendation=resolved_recommendation,
            flight_options=[],
            flight_periods=flight_periods or [],
            transport_comparison=comparison or TransportComparison(),
            weather_advice=weather_advice or WeatherAdvice(),
            date_is_recommended=date_is_recommended,
            assumed_days=assumed_days,
            driving_recommended=resolved_recommendation.mode == "drive",
            show_driving_plan=show_driving_plan,
        )

    @staticmethod
    def _summary(poi: dict[str, Any], reason: str) -> AttractionSummary:
        return AttractionSummary(
            poi_id=poi["poi_id"],
            name=poi["name"],
            province=poi.get("province") or "",
            city=poi.get("city") or "",
            district=poi.get("district") or "",
            address=poi.get("address") or "",
            location=poi.get("location") or "",
            rating=float(poi.get("rating") or 0),
            cost=float(poi.get("cost") or 0),
            type=poi.get("type") or "",
            photos=list(poi.get("photos") or [])[:3],
            source=SourceRef.model_validate(poi["source"]),
            rank_reason=reason,
        )
