from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from ..agent.ticket_periods import build_ticket_periods, summarize_trains
from ..agent.tools import TOOL_SCHEMAS
from ..config import PROJECT_DIR, get_settings
from ..providers.amap import AmapClient, ProviderError
from ..providers.flights import SerpApiFlightProvider
from ..providers.official import OfficialPageProvider
from ..providers.rail import Train12306Client
from ..resilience import CircuitOpenError, provider_guard


def normalize_call_result(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        return {"ok": False, "error": "MCP 工具返回错误", "raw": _content_text(result)}
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    text = _content_text(result)
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"ok": True, "data": parsed}
        except json.JSONDecodeError:
            return {"ok": True, "data": text}
    return {"ok": True, "data": None}


def _content_text(result: Any) -> str:
    chunks: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if isinstance(item, dict):
            text = item.get("text") or text
        if text:
            chunks.append(str(text))
    return "\n".join(chunks)


class McpToolClient:
    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        transport: str | None = None,
        url: str | None = None,
    ):
        settings = get_settings()
        self.command = command or sys.executable
        self.args = args or ["-m", "backend.app.mcp.server", "--transport", "stdio"]
        self.transport = transport or settings.mcp_transport
        self.url = url or settings.mcp_url
        self.timeout_seconds = settings.agent_timeout_seconds
        self.session: ClientSession | None = None
        self._stack = AsyncExitStack()
        self._tools: list[Any] = []

    async def start(self) -> None:
        if self.session is not None:
            return
        if self.transport in {"streamable-http", "http"}:
            streams = await self._stack.enter_async_context(streamable_http_client(self.url))
            read_stream, write_stream = streams[0], streams[1]
        elif self.transport == "sse":
            streams = await self._stack.enter_async_context(sse_client(self.url))
            read_stream, write_stream = streams[0], streams[1]
        else:
            params = StdioServerParameters(
                command=self.command,
                args=self.args,
                cwd=str(PROJECT_DIR),
                env=os.environ.copy(),
            )
            read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream, read_timeout_seconds=self.timeout_seconds)
        )
        await self.session.initialize()
        result = await self.session.list_tools()
        self._tools = list(getattr(result, "tools", []) or [])

    async def stop(self) -> None:
        if self.session is not None:
            self.session = None
            await self._stack.aclose()

    async def list_tools(self) -> list[Any]:
        await self.start()
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        if self.session is None:
            return {"ok": False, "error": "MCP Client 未启动"}
        try:
            return await provider_guard.run(
                f"mcp:{name}",
                lambda: self._call_tool_once(name, arguments),
                timeout_seconds=self.timeout_seconds,
            )
        except CircuitOpenError as exc:
            return {"ok": False, "error": str(exc), "retryable": True, "circuit_open": True}
        except Exception as exc:
            return {"ok": False, "error": f"MCP 调用失败：{exc}", "mcp_error": True, "retryable": True}

    async def _call_tool_once(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            return {"ok": False, "error": "MCP Client 未启动", "retryable": True}
        result = await self.session.call_tool(name, arguments, read_timeout_seconds=self.timeout_seconds)
        normalized = normalize_call_result(result)
        normalized.setdefault("retryable", not normalized.get("ok", False))
        return normalized


class McpToolRegistry:
    def __init__(
        self,
        client: McpToolClient,
        allowed_names: list[str],
        preloaded: dict[str, dict[str, Any]] | None = None,
        preferences: dict[str, Any] | None = None,
    ):
        self.client = client
        self.transport_name = "mcp"
        self.allowed_names = set(allowed_names)
        self.preloaded = preloaded or {}
        self.preferences = preferences or {}
        self._tools: dict[str, Any] = {}

    async def initialize(self) -> None:
        tools = await self.client.list_tools()
        self._tools = {str(getattr(tool, "name", "")): tool for tool in tools if getattr(tool, "name", "") in self.allowed_names}

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if not tool and name == "get_preferences":
                tool = TOOL_SCHEMAS["get_preferences"]["function"]
                result.append({"type": "function", "function": tool})
                continue
            if not tool:
                continue
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(getattr(tool, "description", "") or ""),
                    "parameters": dict(getattr(tool, "input_schema", {}) or getattr(tool, "inputSchema", {}) or {}),
                },
            })
        return result

    def transport_for(self, name: str) -> str:
        return "local" if name == "get_preferences" else "mcp"

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.allowed_names:
            return {"ok": False, "error": f"工具 {name} 不在当前 MCP 白名单中"}
        if name == "get_preferences":
            return {"ok": True, "preferences": self.preferences, "attempts": 0, "cache_hit": True}
        if name in self.preloaded:
            payload = dict(self.preloaded[name])
            payload.setdefault("attempts", 0)
            payload["cache_hit"] = True
            return payload
        return await self.client.call_tool(name, arguments)


class McpDataClient:
    """Provider-shaped facade over the MCP tool plane."""

    def __init__(self, client: McpToolClient):
        self.client = client

    async def _payload(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.call_tool(name, arguments)
        if not result.get("ok"):
            raise ProviderError(str(result.get("error") or f"MCP 工具 {name} 调用失败"))
        return result

    async def china_provinces(self) -> list[dict[str, Any]]:
        return list((await self._payload("get_provinces", {})).get("provinces") or [])

    async def search_pois(self, keywords: str, city: str = "", types: str = "", offset: int = 25, page: int = 1) -> list[dict[str, Any]]:
        return list((await self._payload("search_amap", {"keywords": keywords, "city": city, "types": types})).get("items") or [])

    async def poi_detail(self, poi_id: str) -> dict[str, Any]:
        return dict((await self._payload("get_attraction_detail", {"poi_id": poi_id})).get("poi") or {})

    async def search_nearby(self, keywords: str, location: str, *, radius: int = 5000, offset: int = 20) -> list[dict[str, Any]]:
        result = await self._payload("get_nearby", {"keywords": keywords, "location": location, "radius": radius, "offset": offset})
        return list(result.get("items") or [])

    async def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        result = await self._payload("geocode", {"address": address, "city": city})
        return {key: value for key, value in result.items() if key != "ok"}

    async def weather(self, city: str) -> dict[str, Any]:
        return dict((await self._payload("get_weather", {"city": city})).get("weather") or {})

    async def driving_route(self, origin: str, destination: str) -> dict[str, Any]:
        return dict((await self._payload("route_estimate", {"origin": origin, "destination": destination})).get("route") or {})

    async def query_train_payload(self, origin: str, destination: str, travel_date: str) -> dict[str, Any]:
        return await self._payload("query_train", {"origin": origin, "destination": destination, "travel_date": travel_date})

    async def query_ticket_periods(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        return list((await self.query_train_payload(origin, destination, travel_date)).get("ticket_periods") or [])

    async def query_flights(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        return list((await self._payload("query_flight", {"origin": origin, "destination": destination, "travel_date": travel_date})).get("flights") or [])

    async def verify_many(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not entries:
            return []
        entry = entries[0]
        result = await self._payload("verify_official_page", {
            "name": str(entry.get("name") or ""),
            "url": str(entry.get("url") or ""),
            "keywords": entry.get("keywords") or [],
        })
        return [{key: value for key, value in result.items() if key != "ok"}]

    async def search_web(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return list((await self._payload("search_web", {"query": query, "limit": limit})).get("items") or [])


class LocalDataClient:
    """Test/local fallback with the same interface as McpDataClient."""

    def __init__(self, amap: AmapClient | None = None, rail: Train12306Client | None = None, official: OfficialPageProvider | None = None, flights: SerpApiFlightProvider | None = None):
        self.amap = amap or AmapClient()
        self.rail = rail or Train12306Client()
        self.official = official or OfficialPageProvider()
        self.flights = flights or SerpApiFlightProvider()

    async def china_provinces(self) -> list[Any]:
        return await self.amap.china_provinces()

    async def search_pois(self, keywords: str, city: str = "", types: str = "", offset: int = 25, page: int = 1) -> list[dict[str, Any]]:
        return await self.amap.search_pois(keywords, city=city, types=types, offset=offset, page=page)

    async def poi_detail(self, poi_id: str) -> dict[str, Any]:
        return await self.amap.poi_detail(poi_id)

    async def search_nearby(self, keywords: str, location: str, *, radius: int = 5000, offset: int = 20) -> list[dict[str, Any]]:
        return await self.amap.search_nearby(keywords, location, radius=radius, offset=offset)

    async def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        return await self.amap.geocode(address, city)

    async def weather(self, city: str) -> dict[str, Any]:
        return await self.amap.weather(city)

    async def driving_route(self, origin: str, destination: str) -> dict[str, Any]:
        return await self.amap.driving_route(origin, destination)

    async def query_train_payload(self, origin: str, destination: str, travel_date: str) -> dict[str, Any]:
        trains = await self.rail.query(origin, destination, travel_date)
        periods = build_ticket_periods(trains)
        return {
            "ok": True,
            "ticket_periods": [item.model_dump(mode="json") for item in periods],
            "train_count": len(trains),
            "train_summary": summarize_trains(trains),
            "no_service": not trains,
            "no_tickets": bool(trains) and not periods,
        }

    async def query_ticket_periods(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        return list((await self.query_train_payload(origin, destination, travel_date)).get("ticket_periods") or [])

    async def query_flights(self, origin: str, destination: str, travel_date: str) -> list[dict[str, Any]]:
        return await self.flights.search(origin, destination, travel_date)

    async def verify_many(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await self.official.verify_many(entries)

    async def search_web(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return await self.official.search_web(query, limit)
