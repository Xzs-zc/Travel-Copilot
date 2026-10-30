from __future__ import annotations

from typing import Any, Protocol


class ToolGateway(Protocol):
    transport_name: str

    def schemas(self, names: list[str]) -> list[dict[str, Any]]: ...

    def transport_for(self, name: str) -> str: ...

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


FINAL_TOOLS: dict[str, dict[str, Any]] = {
    "submit_attraction_page": {
        "type": "function",
        "function": {
            "name": "submit_attraction_page",
            "description": "提交景区页结构化数据。所有事实必须来自工具结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "travel_tips": {"type": "array", "items": {"type": "string"}},
                    "ticket_price": {"type": "string"},
                    "open_time": {"type": "string"},
                    "official_url": {"type": "string"},
                    "official_excerpts": {"type": "array", "items": {"type": "string"}},
                    "hero_image": {"type": "string"},
                    "sub_spots": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                },
                "required": ["summary", "travel_tips"],
            },
        },
    },
    "submit_transport_plan": {
        "type": "function",
        "function": {
            "name": "submit_transport_plan",
            "description": "提交出行页祝语和注意事项。路线与余票由工具结果直接聚合，不提交具体车次。",
            "parameters": {
                "type": "object",
                "properties": {
                    "caveats": {"type": "array", "items": {"type": "string"}},
                    "blessing": {"type": "string"},
                    "recommendation": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["drive", "high_speed_rail", "flight", "high_speed_rail_or_flight", "mixed"]},
                            "summary": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "required": ["blessing"],
            },
        },
    },
}
