from __future__ import annotations

import argparse
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..agent.ticket_periods import build_ticket_periods, summarize_trains
from ..agent.transport_hubs import resolve_transport_hubs as resolve_hubs
from ..providers.amap import AmapClient
from ..providers.flights import SerpApiFlightProvider
from ..providers.official import OfficialPageProvider
from ..providers.rail import Train12306Client


def create_server() -> MCPServer:
    server = MCPServer(
        "travel-tools",
        version="0.4.0",
        instructions="中国境内旅行规划所需的实时数据工具。事实必须来自真实 Provider。",
    )
    amap = AmapClient()
    rail = Train12306Client()
    official = OfficialPageProvider()
    flights = SerpApiFlightProvider()

    @server.tool(description="MCP Server 健康检查。")
    def health_check() -> dict[str, Any]:
        return {"ok": True, "server": "travel-tools", "version": "0.4.0"}

    @server.tool(description="查询高德省级行政区域。")
    async def get_provinces() -> dict[str, Any]:
        try:
            provinces = await amap.china_provinces()
            return {"ok": True, "provinces": [item.model_dump(mode="json") for item in provinces]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="在高德地图中按关键词搜索景区或地点。")
    async def search_amap(keywords: str, city: str = "", types: str = "") -> dict[str, Any]:
        try:
            items = await amap.search_pois(keywords, city=city, types=types, offset=25)
            return {"ok": True, "items": items}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="根据高德 POI ID 获取景区详情和周边小景点。")
    async def get_attraction_detail(poi_id: str) -> dict[str, Any]:
        try:
            poi = await amap.poi_detail(poi_id)
            nearby = await amap.search_nearby("景点", poi["location"], radius=8000, offset=10) if poi.get("location") else []
            return {"ok": True, "poi": poi, "nearby": nearby}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="根据高德坐标搜索周边地点。")
    async def get_nearby(keywords: str, location: str, radius: int = 5000, offset: int = 20) -> dict[str, Any]:
        try:
            return {"ok": True, "items": await amap.search_nearby(keywords, location, radius=radius, offset=offset)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="将地址解析为高德经纬度。")
    async def geocode(address: str, city: str = "") -> dict[str, Any]:
        try:
            return {"ok": True, **await amap.geocode(address, city)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="使用高德解析出发地和目的地附近的火车站、机场与候选中转枢纽。")
    async def resolve_transport_hubs(origin: str, destination: str, radius_m: int = 120000) -> dict[str, Any]:
        try:
            return await resolve_hubs(amap, origin, destination, radius_m=radius_m)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="使用高德计算两地驾车距离、时间和过路费。")
    async def route_estimate(origin: str, destination: str) -> dict[str, Any]:
        try:
            origin_info = {} if "," in origin else await amap.geocode(origin)
            destination_info = {} if "," in destination else await amap.geocode(destination)
            origin_location = origin if "," in origin else origin_info["location"]
            destination_location = destination if "," in destination else destination_info["location"]
            route = await amap.driving_route(origin_location, destination_location)
            route.update({
                "origin_name": origin_info.get("formatted_address") or origin,
                "destination_name": destination_info.get("formatted_address") or destination,
                "origin_location": origin_location,
                "destination_location": destination_location,
                "origin_province": origin_info.get("province") or "",
                "origin_city": origin_info.get("city") or "",
                "origin_district": origin_info.get("district") or "",
                "destination_province": destination_info.get("province") or "",
                "destination_city": destination_info.get("city") or "",
                "destination_district": destination_info.get("district") or "",
            })
            return {"ok": True, "route": route}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="获取城市或 adcode 的近期天气。")
    async def get_weather(city: str) -> dict[str, Any]:
        try:
            return {"ok": True, "weather": await amap.weather(city)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="查询 12306 实时车次并聚合为余票时段，不返回具体车次。")
    async def query_train(origin: str, destination: str, travel_date: str) -> dict[str, Any]:
        try:
            items = await rail.query(origin, destination, travel_date)
            periods = build_ticket_periods(items)
            return {
                "ok": True,
                "ticket_periods": [item.model_dump(mode="json") for item in periods],
                "train_count": len(items),
                "train_summary": summarize_trains(items),
                "no_service": not items,
                "no_tickets": bool(items) and not periods,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="查询 Google Flights 指定日期的航班价格、耗时和转机信息。")
    async def query_flight(origin: str, destination: str, travel_date: str) -> dict[str, Any]:
        try:
            return {"ok": True, "flights": await flights.search(origin, destination, travel_date)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="只读核验景区官网的开放时间、票价和预约信息，禁止 OTA 和验证码页面。")
    async def verify_official_page(name: str, url: str, keywords: list[str] | None = None) -> dict[str, Any]:
        try:
            result = (await official.verify_many([{"name": name, "url": url, "keywords": keywords or []}]))[0]
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description="通过公开搜索结果查找官网或补充来源，只返回标题和链接，不抓取正文。")
    async def search_web(query: str, limit: int = 5) -> dict[str, Any]:
        try:
            return {"ok": True, "items": await official.search_web(query, limit)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel Copilot MCP tool server")
    parser.add_argument("--transport", choices=("stdio", "sse", "streamable-http"), default="stdio")
    args = parser.parse_args()
    server = create_server()
    server.run(args.transport)


if __name__ == "__main__":
    main()
