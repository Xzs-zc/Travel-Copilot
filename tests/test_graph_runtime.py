import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.agent.graph.runtime import LangGraphRuntime
from backend.app.agent.tools import ToolRegistry
from backend.app.providers.amap import AmapClient
from backend.app.providers.official import OfficialPageProvider
from backend.app.providers.rail import Train12306Client


class CompletionClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=response)], usage=None)


def tool_call(call_id, name, arguments="{}"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_graph_runtime_uses_whitelisted_tool_and_finishes():
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[tool_call("call-1", "get_preferences")]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-2", "submit_transport_plan", '{"blessing":"愿君此行，山河入梦。","caveats":[]}')]),
    ])
    registry = ToolRegistry(
        amap=AmapClient(api_key="x"),
        rail=Train12306Client(),
        official=OfficialPageProvider(),
        preferences={},
    )
    runtime = LangGraphRuntime(client=client, checkpointer=None)
    events = []
    async for event in runtime.run_stream(
        task="transport_plan",
        system_prompt="交通规划",
        user_prompt="上海到杭州",
        tools=registry,
        tool_names=["get_preferences"],
        final_tool="submit_transport_plan",
        constraints={"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"},
    ):
        events.append(event)
    names = [event.event for event in events]
    assert "tool_start" in names
    assert "tool_result" in names
    assert names[-1] == "done"
    assert events[-1].data["blessing"] == "愿君此行，山河入梦。"


class BatchGateway:
    transport_name = "mcp"

    def __init__(self):
        self.active = 0
        self.max_active = 0

    def schemas(self, names):
        return []

    def transport_for(self, name):
        return "mcp"

    async def execute(self, name, arguments):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        if name == "get_weather":
            return {"ok": True, "weather": {"casts": [{"date": "2026-10-01", "dayweather": "晴"}]}}
        return {"ok": True, "preferences": {}}


@pytest.mark.asyncio
async def test_graph_runtime_runs_independent_tools_concurrently():
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[
            tool_call("call-1", "get_preferences"),
            tool_call("call-2", "get_weather", '{"city":"杭州"}'),
        ]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-3", "submit_transport_plan", '{"blessing":"一路顺风","caveats":[]}')]),
    ])
    gateway = BatchGateway()
    runtime = LangGraphRuntime(client=client, checkpointer=None)
    events = []
    async for event in runtime.run_stream(
        task="transport_plan",
        system_prompt="交通规划",
        user_prompt="上海到杭州",
        tools=gateway,
        tool_names=["get_preferences", "get_weather"],
        final_tool="submit_transport_plan",
        constraints={"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"},
    ):
        events.append(event)
    assert gateway.max_active == 2
    assert [event.event for event in events].count("tool_result") == 2
    assert events[-1].event == "done"


class PolicyGateway(BatchGateway):
    def schemas(self, names):
        return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in names]

    async def execute(self, name, arguments):
        if name == "query_train":
            return {
                "ok": True,
                "ticket_periods": [],
                "train_count": 1,
                "train_summary": {"train_count": 1, "min_duration_minutes": 420},
            }
        return {"ok": True, "weather": {"casts": []}}


@pytest.mark.asyncio
async def test_transport_policy_gate_opens_flight_after_long_rail():
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[tool_call("call-1", "query_train", '{"origin":"上海","destination":"北京","travel_date":"2026-10-06"}')]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-2", "get_weather", '{"city":"北京"}')]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-3", "submit_transport_plan", '{"blessing":"一路顺风","caveats":[]}')]),
    ])
    runtime = LangGraphRuntime(client=client, checkpointer=None)
    events = []
    async for event in runtime.run_stream(
        task="transport_plan",
        system_prompt="交通规划",
        user_prompt="上海到北京",
        tools=PolicyGateway(),
        tool_names=["query_train", "query_flight", "get_weather"],
        final_tool="submit_transport_plan",
        constraints={"origin": "上海", "destination": "北京", "travel_date": "2026-10-06"},
    ):
        events.append(event)
    second_tools = [item["function"]["name"] for item in client.calls[1]["tools"]]
    assert "query_flight" in second_tools
    assert events[-1].event == "done"


@pytest.mark.asyncio
async def test_graph_runtime_persists_checkpoint(tmp_path):
    connection = await aiosqlite.connect(str(tmp_path / "graph.db"))
    saver = AsyncSqliteSaver(connection)
    await saver.setup()
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[tool_call("call-1", "get_preferences")]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-2", "submit_transport_plan", '{"blessing":"愿君此行，山河入梦。","caveats":[]}')]),
    ])
    registry = ToolRegistry(
        amap=AmapClient(api_key="x"),
        rail=Train12306Client(),
        official=OfficialPageProvider(),
        preferences={},
    )
    runtime = LangGraphRuntime(client=client, checkpointer=saver)
    async for _ in runtime.run_stream(
        task="transport_plan",
        system_prompt="交通规划",
        user_prompt="上海到杭州",
        tools=registry,
        tool_names=["get_preferences"],
        final_tool="submit_transport_plan",
        constraints={"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01", "thread_id": "checkpoint-test"},
    ):
        pass
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": "checkpoint-test"}})
    assert checkpoint is not None
    values = checkpoint.checkpoint.get("channel_values") or {}
    assert values.get("final_result", {}).get("blessing") == "愿君此行，山河入梦。"
    await connection.close()


class MissingAmap:
    async def poi_detail(self, poi_id):
        return {"poi_id": "", "name": "", "source": {"title": "高德地图", "provider": "高德地图"}}


@pytest.mark.asyncio
async def test_graph_runtime_replans_after_invalid_tool_result():
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[tool_call("call-1", "get_attraction_detail", '{"poi_id":"missing"}')]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-2", "submit_attraction_page", '{"summary":"暂无可靠资料","travel_tips":[]}')]),
    ])
    registry = ToolRegistry(
        amap=MissingAmap(),
        rail=Train12306Client(),
        official=OfficialPageProvider(),
        preferences={},
    )
    runtime = LangGraphRuntime(client=client, checkpointer=None)
    events = []
    async for event in runtime.run_stream(
        task="attraction_page",
        system_prompt="整理景区",
        user_prompt="missing",
        tools=registry,
        tool_names=["get_attraction_detail"],
        final_tool="submit_attraction_page",
        constraints={"poi_id": "missing"},
    ):
        events.append(event)
    names = [event.event for event in events]
    assert "validation" in names
    assert "replan" in names
    assert names[-1] == "done"


class ShortTransferGateway(BatchGateway):
    def schemas(self, names):
        return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in names]

    async def execute(self, name, arguments):
        if name != "query_train":
            return {"ok": True, "weather": {"casts": []}}
        origin = str(arguments.get("origin") or "")
        destination = str(arguments.get("destination") or "")
        if origin == "深圳" and "崇左" in destination:
            return {"ok": False, "error": "12306 未返回 深圳 到 崇左市 的可用车次：没有可购车次"}
        if origin == "深圳" and destination == "南宁":
            return {
                "ok": True,
                "train_count": 3,
                "train_summary": {"train_count": 3, "high_speed_count": 3, "min_duration_minutes": 214},
                "ticket_periods": [{"label": "15:00–17:59", "status": "tight", "summary": "共 2 个车次，0 个有余票"}],
            }
        if origin == "南宁" and "崇左" in destination:
            return {
                "ok": True,
                "train_count": 23,
                "train_summary": {"train_count": 23, "high_speed_count": 22, "min_duration_minutes": 30},
                "ticket_periods": [{"label": "18:00–20:59", "status": "tight", "summary": "共 11 个车次，0 个有余票"}],
            }
        return {"ok": False, "error": "unexpected route"}


@pytest.mark.asyncio
async def test_transport_policy_gate_opens_flight_when_direct_rail_is_unavailable():
    client = CompletionClient([
        SimpleNamespace(content="", tool_calls=[tool_call("call-1", "query_train", '{"origin":"深圳","destination":"崇左市","travel_date":"2026-10-06"}')]),
        SimpleNamespace(content="", tool_calls=[
            tool_call("call-2", "query_train", '{"origin":"深圳","destination":"南宁","travel_date":"2026-10-06"}'),
            tool_call("call-3", "query_train", '{"origin":"南宁","destination":"崇左市","travel_date":"2026-10-06"}'),
        ]),
        SimpleNamespace(content="", tool_calls=[tool_call("call-4", "submit_transport_plan", '{"blessing":"证件带好，一路顺风。","caveats":[]}')]),
    ])
    runtime = LangGraphRuntime(client=client, checkpointer=None)
    events = []
    async for event in runtime.run_stream(
        task="transport_plan",
        system_prompt="交通规划",
        user_prompt="深圳到崇左",
        tools=ShortTransferGateway(),
        tool_names=["query_train", "query_flight", "get_weather"],
        final_tool="submit_transport_plan",
        constraints={"origin": "深圳", "destination": "崇左市", "travel_date": "2026-10-06"},
    ):
        events.append(event)
    final_tools = [item["function"]["name"] for item in client.calls[2]["tools"]]
    assert "query_flight" in final_tools
    assert any(event.event == "status" and "开放中转铁路" in str(event.data) for event in events)
    assert events[-1].event == "done"


def test_compact_tool_result_drops_heavy_route_path():
    compact = LangGraphRuntime._compact_tool_result(
        "route_estimate",
        {
            "ok": True,
            "route": {
                "origin_name": "梁山",
                "destination_name": "林芝",
                "distance_m": 2100000,
                "duration_s": 90000,
                "path": [[120.0, 30.0]] * 10000,
            },
        },
        {"origin": "梁山", "destination": "林芝"},
        {"validation": {"errors": [], "warnings": []}},
    )
    assert compact["route"]["distance_m"] == 2100000
    assert "path" not in compact["route"]
