from __future__ import annotations

from typing import Any

from ..providers.amap import AmapClient, ConfigurationError
from ..providers.flights import SerpApiFlightProvider
from ..providers.official import OfficialPageProvider
from ..providers.rail import Train12306Client
from ..resilience import CircuitOpenError, provider_guard
from ..schemas import SourceRef
from .ticket_periods import build_ticket_periods, summarize_trains
from .transport_hubs import resolve_transport_hubs
from .validators import validate_arguments


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_amap": {
        "type": "function",
        "function": {
            "name": "search_amap",
            "description": "在高德地图中搜索景区或地点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "string"},
                    "city": {"type": "string", "description": "省市名称或 adcode，可留空"},
                    "types": {"type": "string", "description": "高德类型编码，可留空"},
                },
                "required": ["keywords"],
            },
        },
    },
    "get_attraction_detail": {
        "type": "function",
        "function": {
            "name": "get_attraction_detail",
            "description": "根据高德 POI ID 获取景区详情、图片和周边小景点。",
            "parameters": {"type": "object", "properties": {"poi_id": {"type": "string"}}, "required": ["poi_id"]},
        },
    },
    "verify_official_page": {
        "type": "function",
        "function": {
            "name": "verify_official_page",
            "description": "只读打开景区官方网站，提取开放时间、票价和预约信息。不得访问 OTA、登录或验证码页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "url"],
            },
        },
    },
    "search_web": {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "通过公开搜索结果查找景区官网或补充来源，仅返回标题和链接，不抓取正文。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, "required": ["query"]},
        },
    },
    "get_weather": {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取目的地近期天气。",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        },
    },
    "query_train": {
        "type": "function",
        "function": {
            "name": "query_train",
            "description": "查询 12306 指定日期、出发站和到达站的实时车次、票价与余票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["origin", "destination", "travel_date"],
            },
        },
    },
    "query_flight": {
        "type": "function",
        "function": {
            "name": "query_flight",
            "description": "查询 Google Flights 指定日期、出发城市和到达城市的航班价格、总耗时和转机信息。仅在高铁路程超过 6 小时或无高铁直达时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["origin", "destination", "travel_date"],
            },
        },
    },
    "resolve_transport_hubs": {
        "type": "function",
        "function": {
            "name": "resolve_transport_hubs",
            "description": "使用高德解析出发地和目的地附近的火车站、机场与候选中转枢纽。用于缩小铁路和航班查询范围，不提供车次、余票或航班时刻。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "radius_m": {"type": "integer", "default": 120000, "description": "附近枢纽搜索半径，建议 50000-200000 米"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    "route_estimate": {
        "type": "function",
        "function": {
            "name": "route_estimate",
            "description": "使用高德计算两地驾车距离、时间和过路费。",
            "parameters": {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}}, "required": ["origin", "destination"]},
        },
    },
    "get_preferences": {
        "type": "function",
        "function": {"name": "get_preferences", "description": "读取用户的长期出发地和交通偏好。", "parameters": {"type": "object", "properties": {}}},
    },
}


class ToolRegistry:
    def __init__(self, *, amap: AmapClient, rail: Train12306Client, official: OfficialPageProvider, preferences: dict[str, Any], preloaded: dict[str, dict[str, Any]] | None = None, flights: SerpApiFlightProvider | None = None):
        self.amap = amap
        self.rail = rail
        self.official = official
        self.flights = flights or SerpApiFlightProvider()
        self.preferences = preferences
        self.transport_name = "local"
        self.preloaded = preloaded or {}
        self.call_counts: dict[str, int] = {}
        self.call_limits = {"search_web": 2, "verify_official_page": 2, "route_estimate": 2}

    @staticmethod
    def schemas(names: list[str]) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[name] for name in names if name in TOOL_SCHEMAS]

    @staticmethod
    def transport_for(name: str) -> str:
        return "local"

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized, errors = validate_arguments(name, arguments)
        if errors or normalized is None:
            return {"ok": False, "error": "invalid_arguments", "validation": {"errors": errors}, "attempts": 0}
        if name in self.preloaded:
            payload = dict(self.preloaded[name])
            payload.setdefault("attempts", 0)
            payload["cache_hit"] = True
            return payload
        self.call_counts[name] = self.call_counts.get(name, 0) + 1
        if name in self.call_limits and self.call_counts[name] > self.call_limits[name]:
            return {"ok": False, "error": f"工具 {name} 已达到本次任务调用上限", "attempts": 0}

        try:
            result = await provider_guard.run(
                f"tool:{name}",
                lambda: self._execute_once(name, normalized),
            )
            if isinstance(result, dict):
                return result
            return {"ok": True, "data": result}
        except ConfigurationError as exc:
            return {"ok": False, "error": str(exc), "attempts": 1, "retryable": False}
        except CircuitOpenError as exc:
            return {"ok": False, "error": str(exc), "attempts": 0, "retryable": True, "circuit_open": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "tool_failed", "retryable": True}

    async def _execute_once(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_amap":
            pois = await self.amap.search_pois(
                str(arguments.get("keywords") or ""),
                city=str(arguments.get("city") or ""),
                types=str(arguments.get("types") or ""),
            )
            return {"ok": True, "items": pois[:15]}
        if name == "get_attraction_detail":
            detail = await self.amap.poi_detail(str(arguments.get("poi_id") or ""))
            nearby = []
            if detail.get("location"):
                try:
                    nearby = await self.amap.search_nearby("景点", detail["location"], radius=8000, offset=10)
                except Exception:
                    nearby = []
            return {"ok": True, "poi": detail, "nearby": nearby}
        if name == "verify_official_page":
            result = (await self.official.verify_many([{
                "name": str(arguments.get("name") or ""),
                "url": str(arguments.get("url") or ""),
                "keywords": arguments.get("keywords") or [],
            }]))[0]
            return {"ok": True, **result}
        if name == "search_web":
            items = await self.official.search_web(str(arguments.get("query") or ""), int(arguments.get("limit") or 5))
            return {"ok": True, "items": items}
        if name == "get_weather":
            return {"ok": True, "weather": await self.amap.weather(str(arguments.get("city") or ""))}
        if name == "query_train":
            items = await self.rail.query(
                str(arguments.get("origin") or ""),
                str(arguments.get("destination") or ""),
                str(arguments.get("travel_date") or ""),
            )
            periods = build_ticket_periods(items)
            return {
                "ok": True,
                "ticket_periods": [item.model_dump(mode="json") for item in periods],
                "train_count": len(items),
                "train_summary": summarize_trains(items),
                "no_service": not items,
                "no_tickets": bool(items) and not periods,
            }
        if name == "query_flight":
            flights = await self.flights.search(
                str(arguments.get("origin") or ""),
                str(arguments.get("destination") or ""),
                str(arguments.get("travel_date") or ""),
            )
            return {"ok": True, "flights": flights}
        if name == "resolve_transport_hubs":
            return await resolve_transport_hubs(
                self.amap,
                str(arguments.get("origin") or ""),
                str(arguments.get("destination") or ""),
                radius_m=int(arguments.get("radius_m") or 120000),
            )
        if name == "route_estimate":
            origin_name = str(arguments.get("origin") or "")
            destination_name = str(arguments.get("destination") or "")
            origin = origin_name
            destination = destination_name
            if "," not in origin:
                origin = (await self.amap.geocode(origin))["location"]
            if "," not in destination:
                destination = (await self.amap.geocode(destination))["location"]
            route = await self.amap.driving_route(origin, destination)
            route.update({
                "origin_name": origin_name,
                "destination_name": destination_name,
                "origin_location": origin,
                "destination_location": destination,
            })
            return {"ok": True, "route": route}
        if name == "get_preferences":
            return {"ok": True, "preferences": self.preferences}
        return {"ok": False, "error": f"未知工具：{name}"}


def source_from_dict(data: dict[str, Any]) -> SourceRef:
    return SourceRef(**data)
