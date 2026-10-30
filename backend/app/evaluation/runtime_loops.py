from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

from backend.app.agent.deterministic import DeterministicRuntime
from backend.app.agent.graph.runtime import LangGraphRuntime
from backend.app.agent.tools import ToolRegistry

from .models import EvaluationCaseResult


class FakeCompletions:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]):
        self.calls = calls
        self.index = 0

    async def create(self, **kwargs):
        name, arguments = self.calls[self.index]
        self.index += 1
        call = SimpleNamespace(id=f"call-{self.index}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
        message = SimpleNamespace(content="", tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class FakeClient:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]):
        self.chat = SimpleNamespace(completions=FakeCompletions(calls))


class LoopAmap:
    def __init__(self, *, empty_search: bool = False):
        self.empty_search = empty_search

    async def search_pois(self, *args, **kwargs):
        return []

    async def geocode(self, address: str, city: str = ""):
        return {"location": "121.473667,31.230525" if address == "上海" else "120.209903,30.246566"}

    async def driving_route(self, origin: str, destination: str):
        return {
            "distance_m": 177000,
            "duration_s": 7900,
            "tolls": 71,
            "path": [[121.473667, 31.230525], [120.209903, 30.246566]],
            "source": {"title": "高德驾车路线", "provider": "高德地图", "url": "https://www.amap.com/", "excerpt": "评测"},
        }


class NoopRail:
    async def query(self, *args, **kwargs):
        return []


class NoopOfficial:
    async def search_web(self, *args, **kwargs):
        return []

    async def verify_many(self, *args, **kwargs):
        return [{"status": "pending", "url": "", "matches": []}]


def run_runtime_loop_cases(variant: str = "full") -> list[EvaluationCaseResult]:
    return [
        _route_loop_case(variant),
        _recovery_loop_case(variant),
    ]


def _runtime(variant: str, calls: list[tuple[str, dict[str, Any]]]):
    if variant == "no_llm":
        return DeterministicRuntime()
    return LangGraphRuntime(client=FakeClient(calls), checkpointer=None)


def _route_loop_case(variant: str) -> EvaluationCaseResult:
    start = time.perf_counter()
    runtime = _runtime(variant, [
        ("route_estimate", {"origin": "上海", "destination": "杭州"}),
        ("submit_transport_plan", {"blessing": "愿君此行，湖山相迎。", "caveats": []}),
    ])
    registry = ToolRegistry(amap=LoopAmap(), rail=NoopRail(), official=NoopOfficial(), preferences={})
    events = _collect(runtime, registry, task="transport_plan", constraints={"origin": "上海", "destination": "杭州", "travel_date": "2026-10-01"}, final_tool="submit_transport_plan", tool_names=["route_estimate"])
    names = [event.event for event in events]
    failures = []
    if "plan" not in names or "tool_start" not in names or "evidence" not in names or names[-1] != "done":
        failures.append("Agent Runtime 工具循环未完整执行")
    return _case("runtime-route-loop", "真实 Agent Runtime 工具循环", start, failures, {"events": names}, ["runtime_loop_incomplete"] if failures else [])


def _recovery_loop_case(variant: str) -> EvaluationCaseResult:
    start = time.perf_counter()
    runtime = _runtime(variant, [
        ("search_amap", {"keywords": "不存在的景区"}),
        ("submit_attraction_page", {"summary": "暂无可靠资料", "travel_tips": []}),
    ])
    registry = ToolRegistry(amap=LoopAmap(empty_search=True), rail=NoopRail(), official=NoopOfficial(), preferences={})
    events = _collect(runtime, registry, task="attraction_page", constraints={"poi_id": "missing"}, final_tool="submit_attraction_page", tool_names=["search_amap"])
    names = [event.event for event in events]
    failures = []
    if "validation" not in names or "replan" not in names or names[-1] != "done":
        failures.append("Agent Runtime 未执行校验后重规划")
    return _case("runtime-replan-loop", "Agent Runtime 校验与重规划循环", start, failures, {"events": names}, ["runtime_replan_missing"] if failures else [])


def _collect(runtime: Any, registry: ToolRegistry, *, task: str, constraints: dict[str, Any], final_tool: str, tool_names: list[str]):
    events = []
    async def collect():
        async for event in runtime.run_stream(
            task=task,
            system_prompt="评测工具循环",
            user_prompt="执行评测",
            tools=registry,
            tool_names=tool_names,
            final_tool=final_tool,
            constraints=constraints,
        ):
            events.append(event)
    asyncio.run(collect())
    return events


def _case(case_id: str, name: str, start: float, failures: list[str], metrics: dict[str, Any], root_causes: list[str]) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        id=case_id,
        name=name,
        category="agent",
        suite="offline",
        hard_gate=True,
        passed=not failures,
        score=100 if not failures else 0,
        duration_ms=int((time.perf_counter() - start) * 1000),
        metrics=metrics,
        failures=failures,
        root_causes=root_causes,
    )
