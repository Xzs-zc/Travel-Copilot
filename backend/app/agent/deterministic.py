from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..schemas import AgentEvent
from .contracts import ToolGateway
from .state import PlanStep, TaskState, build_task_state
from .validators import validate_final_result, validate_tool_result


class DeterministicRuntime:
    """No-LLM policy: deterministic tools, templates and validators only."""

    async def run_stream(
        self,
        *,
        task: str,
        system_prompt: str,
        user_prompt: str,
        tools: ToolGateway,
        tool_names: list[str],
        final_tool: str,
        constraints: dict[str, Any] | None = None,
        state: TaskState | None = None,
        trace_context: dict[str, str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        run_id = (state.run_id if state else "") or uuid.uuid4().hex[:16]
        state = state or build_task_state(run_id=run_id, task=task, goal=user_prompt, constraints=constraints)
        state.max_replans = 0
        state.status = "running"
        yield AgentEvent(event="state", data={"phase": "planned", "mode": "no_llm", "missing_fields": state.missing_fields}, run_id=run_id, meta={"state": state.model_dump(mode="json")})
        if state.missing_fields:
            state.status = "failed"
            yield AgentEvent(event="error", data={"code": "missing_constraints", "fields": state.missing_fields}, run_id=run_id, meta={"state": state.model_dump(mode="json")})
            return

        captured: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(self._tool_names(task, constraints or {}), start=1):
            if name not in tool_names:
                continue
            arguments = self._arguments(name, constraints or {}, captured)
            plan_step = PlanStep(step=index, tool=name, arguments=arguments, status="running")
            state.plan.append(plan_step)
            yield AgentEvent(event="plan", data=plan_step.model_dump(mode="json"), step=index, run_id=run_id)
            yield AgentEvent(event="tool_start", data={"tool": name, "arguments": arguments}, step=index, run_id=run_id)
            result = await tools.execute(name, arguments)
            validation = validate_tool_result(name, result, arguments)
            plan_step.status = "succeeded" if validation.ok else "failed"
            plan_step.error = ";".join(validation.errors)
            state.tool_calls += 1
            if validation.evidence:
                state.add_evidence(validation.evidence)
                yield AgentEvent(event="evidence", data={"items": [item.model_dump(mode="json") for item in validation.evidence]}, step=index, run_id=run_id)
            if not validation.ok:
                state.errors.extend(validation.errors)
                if not state.warnings:
                    state.status = "running"
            yield AgentEvent(event="tool_result", data={"tool": name, "result": result, "ok": validation.ok, "validation": validation.model_dump(mode="json"), "transport": tools.transport_for(name) if hasattr(tools, "transport_for") else "local"}, step=index, run_id=run_id)
            captured[name] = result

        final = self._final(task, constraints or {}, captured)
        validation = validate_final_result(final_tool, final)
        if not validation.ok:
            state.status = "failed"
            yield AgentEvent(event="error", data={"code": "final_validation_failed", "errors": validation.errors}, run_id=run_id, meta={"state": state.model_dump(mode="json")})
            return
        state.status = "degraded" if state.errors else "succeeded"
        yield AgentEvent(event="done", data=final, run_id=run_id, meta={"state": state.model_dump(mode="json")})

    @staticmethod
    def _tool_names(task: str, constraints: dict[str, Any]) -> list[str]:
        if task == "attraction_page":
            return ["get_attraction_detail", "verify_official_page", "get_weather"]
        if task == "transport_plan":
            return ["get_preferences", "query_train", "route_estimate", "get_weather"]
        if task == "blessing":
            return []
        return []

    @staticmethod
    def _arguments(name: str, constraints: dict[str, Any], captured: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if name == "get_attraction_detail":
            return {"poi_id": str(constraints.get("poi_id") or "")}
        if name == "verify_official_page":
            poi = (captured.get("get_attraction_detail") or {}).get("poi") or {}
            return {"name": str(poi.get("name") or ""), "url": str(poi.get("website") or "")}
        if name == "get_weather":
            return {"city": str(constraints.get("destination") or constraints.get("city") or "")}
        if name == "query_train":
            return {"origin": str(constraints.get("origin") or ""), "destination": str(constraints.get("destination") or ""), "travel_date": str(constraints.get("travel_date") or "")}
        if name == "route_estimate":
            return {"origin": str(constraints.get("origin") or ""), "destination": str(constraints.get("destination") or "")}
        if name == "search_amap":
            return {"keywords": str(constraints.get("query") or "")}
        return {}

    @staticmethod
    def _final(task: str, constraints: dict[str, Any], captured: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if task == "attraction_page":
            poi = (captured.get("get_attraction_detail") or {}).get("poi") or {}
            name = str(poi.get("name") or "该景区")
            address = str(poi.get("address") or "地址暂无可靠来源")
            return {
                "summary": f"{name}的真实 POI 信息来自高德，地址为{address}。",
                "travel_tips": ["开放时间、票价和预约规则请以景区官方最新公告为准。"],
                "open_time": str(poi.get("open_time") or ""),
                "ticket_price": "",
                "official_excerpts": [],
            }
        if task == "transport_plan":
            destination = str(constraints.get("destination") or "目的地")
            return {
                "blessing": (
                    f"山高水远路迢迢，{destination}风光真叫妙；"
                    "身份证和雨伞都带好，出门在外少烦恼。"
                ),
                "caveats": ["当前为无 LLM 回放模式，事实数据仍以工具结果为准。"],
            }
        if task == "blessing":
            destination = str(constraints.get("destination") or "远方")
            return {"blessing": f"愿君此去{destination}，山河入眼，清风伴行。"}
        if task == "province_rank":
            return {"items": []}
        return {}

    @staticmethod
    def province_system_prompt() -> str:
        return "No-LLM deterministic policy"

    @staticmethod
    def attraction_system_prompt() -> str:
        return "No-LLM deterministic policy"

    @staticmethod
    def transport_system_prompt() -> str:
        return "No-LLM deterministic policy"
