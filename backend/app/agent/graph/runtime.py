from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...config import get_settings
from ...observability import finish_observation, observation, observation_context, start_observation, trace_value, update, usage_details
from ...schemas import AgentEvent
from ..contracts import FINAL_TOOLS, ToolGateway
from ..prompts import attraction_system_prompt, transport_system_prompt
from ..state import PlanStep, TaskState, build_task_state
from ..ticket_periods import build_rail_itinerary
from ..validators import validate_arguments, validate_final_result, validate_tool_result


class RuntimeGraphState(TypedDict, total=False):
    run_id: str
    task: str
    goal: str
    constraints: dict[str, Any]
    system_prompt: str
    user_prompt: str
    final_tool: str
    messages: list[dict[str, Any]]
    task_state: TaskState
    allowed_tools: list[str]
    pending_calls: list[dict[str, Any]]
    tool_payloads: dict[str, dict[str, Any]]
    tool_history: list[dict[str, Any]]
    final_result: dict[str, Any] | None
    step: int
    status: str
    error: Any
    error_emitted: bool


class LangGraphRuntime:
    """LangGraph ReAct runtime with task-specific policy gates.

    The graph owns orchestration, state transitions, retries and checkpointing.
    MCP, validation, SSE events and Langfuse reporters remain shared app
    services; orchestration lives exclusively in this graph runtime.
    """

    attraction_system_prompt = staticmethod(attraction_system_prompt)
    transport_system_prompt = staticmethod(transport_system_prompt)

    def __init__(self, client: Any | None = None, *, checkpointer: Any | None = None):
        self.settings = get_settings()
        self.client = client
        if self.client is None and self.settings.deepseek_api_key:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.agent_timeout_seconds,
                max_retries=1,
            )
        if checkpointer is None:
            from .checkpoints import get_graph_checkpointer

            checkpointer = get_graph_checkpointer()
        self.checkpointer = checkpointer

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
        final: dict[str, Any] | None = None
        agent_meta: dict[str, Any] = {}
        agent_span = start_observation(
            name=f"agent:{task}",
            as_type="agent",
            input={"task": task, "constraints": constraints, "user_prompt": user_prompt},
            metadata={"final_tool": final_tool, "tools": tool_names, "runtime": "langgraph"},
            trace_context=trace_context,
        )
        agent_context = observation_context(agent_span) or trace_context
        try:
            async for event in self._run_graph_stream(
                task=task,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=tools,
                tool_names=tool_names,
                final_tool=final_tool,
                constraints=constraints,
                state=state,
                trace_context=agent_context,
            ):
                if event.event == "done" and isinstance(event.data, dict):
                    final = event.data
                    if isinstance(event.meta, dict) and isinstance(event.meta.get("state"), dict):
                        agent_meta = event.meta["state"]
                if event.event == "error":
                    update(agent_span, level="ERROR", status_message=str(event.data))
                yield event
        finally:
            finish_observation(
                agent_span,
                output=trace_value(final),
                metadata={
                    "runtime": "langgraph",
                    "tool_calls": agent_meta.get("tool_calls", 0),
                    "replan_count": agent_meta.get("replan_count", 0),
                    "status": agent_meta.get("status", "succeeded"),
                },
            )

    async def _run_graph_stream(
        self,
        *,
        task: str,
        system_prompt: str,
        user_prompt: str,
        tools: ToolGateway,
        tool_names: list[str],
        final_tool: str,
        constraints: dict[str, Any] | None,
        state: TaskState | None,
        trace_context: dict[str, str] | None,
    ) -> AsyncIterator[AgentEvent]:
        if self.client is None:
            yield AgentEvent(event="error", data="未配置 DEEPSEEK_API_KEY", run_id="")
            return
        if final_tool not in FINAL_TOOLS:
            raise ValueError(f"未知终止工具：{final_tool}")

        run_id = (state.run_id if state else "") or uuid.uuid4().hex[:16]
        task_state = state or build_task_state(run_id=run_id, task=task, goal=user_prompt, constraints=constraints)
        if self.settings.ablation_variant == "no_replan":
            task_state.max_replans = 0
        task_state.status = "running"
        allowed_tools = list(tool_names)
        if task == "transport_plan" and "query_flight" in allowed_tools:
            allowed_tools = [name for name in allowed_tools if name != "query_flight"]

        graph = self._build_graph(
            tools=tools,
            original_tool_names=tool_names,
            trace_context=trace_context,
        )
        thread_id = str((constraints or {}).get("thread_id") or (constraints or {}).get("session_id") or run_id)
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": max(12, self.settings.max_agent_steps * 3 + 5),
        }
        initial_state: RuntimeGraphState = {
            "run_id": run_id,
            "task": task,
            "goal": user_prompt,
            "constraints": dict(constraints or {}),
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "final_tool": final_tool,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "task_state": task_state,
            "allowed_tools": allowed_tools,
            "pending_calls": [],
            "tool_payloads": {
                name: payload for name, payload in dict(getattr(tools, "preloaded", {}) or {}).items()
            },
            "tool_history": [],
            "final_result": None,
            "step": 0,
            "status": "running",
            "error": None,
            "error_emitted": False,
        }
        final_state: RuntimeGraphState | None = None
        try:
            async with asyncio.timeout(self.settings.agent_timeout_seconds):
                async for mode, chunk in graph.astream(
                    initial_state,
                    config=config,
                    stream_mode=["custom", "values"],
                ):
                    if mode == "values":
                        final_state = chunk
                        continue
                    event = AgentEvent.model_validate(chunk)
                    if event.event == "done" and not event.run_id:
                        event.run_id = run_id
                    yield event
        except TimeoutError:
            task_state.status = "failed"
            task_state.errors.append("agent_timeout")
            yield AgentEvent(
                event="error",
                data=f"Agent 超过任务超时 {self.settings.agent_timeout_seconds}s",
                run_id=run_id,
                meta={"state": task_state.model_dump(mode="json")},
            )
            return
        except Exception as exc:
            task_state.status = "failed"
            task_state.errors.append(str(exc))
            yield AgentEvent(
                event="error",
                data=str(exc),
                run_id=run_id,
                meta={"state": task_state.model_dump(mode="json")},
            )
            return

    def _build_graph(
        self,
        *,
        tools: ToolGateway,
        original_tool_names: list[str],
        trace_context: dict[str, str] | None,
    ) -> CompiledStateGraph:
        settings = self.settings

        def state_meta(state: RuntimeGraphState) -> dict[str, Any]:
            task_state = state.get("task_state")
            if isinstance(task_state, TaskState):
                return task_state.model_dump(mode="json")
            return dict(task_state or {})

        def emit(
            writer: Any,
            state: RuntimeGraphState,
            event: str,
            data: Any,
            **extra: Any,
        ) -> None:
            writer({
                "event": event,
                "data": data,
                "step": int(state.get("step") or 0),
                "run_id": state.get("run_id") or "",
                "duration_ms": int(extra.pop("duration_ms", 0)),
                "meta": {"state": state_meta(state)},
                **extra,
            })

        async def agent_node(state: RuntimeGraphState) -> RuntimeGraphState:
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            step = int(state.get("step") or 0) + 1
            current: RuntimeGraphState = {**state, "task_state": task_state, "step": step}
            if step > settings.max_agent_steps:
                task_state.status = "failed"
                task_state.errors.append("max_steps_exceeded")
                current.update({"status": "failed", "error": "max_steps_exceeded", "error_emitted": True})
                emit(writer, current, "error", f"Agent 超过最大步数 {settings.max_agent_steps}")
                return current

            allowed_tools = list(state.get("allowed_tools") or original_tool_names)
            schemas = tools.schemas(allowed_tools) + [FINAL_TOOLS[state["final_tool"]]]
            messages = list(state.get("messages") or [])
            emit(writer, current, "status", "DeepSeek 正在分析任务")
            with observation(
                name=f"llm:{state['task']}",
                as_type="generation",
                input={"messages": trace_value(messages), "tools": trace_value(schemas)},
                model=settings.deepseek_model,
                model_parameters={"temperature": 0.2, "tool_choice": "auto"},
                metadata={"step": step, "task": state["task"], "runtime": "langgraph"},
                trace_context=trace_context,
            ) as generation:
                response = await self.client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=messages,
                    tools=schemas,
                    tool_choice="auto",
                    temperature=0.2,
                )
                usage = self._usage_dict(getattr(response, "usage", None))
                task_state.add_tokens(usage)
                message = response.choices[0].message
                tool_calls = list(message.tool_calls or [])
                update(
                    generation,
                    output=trace_value({
                        "content": message.content or "",
                        "tool_calls": [
                            {"name": call.function.name, "arguments": call.function.arguments}
                            for call in tool_calls
                        ],
                    }),
                    usage_details=usage_details(usage),
                )

            if not tool_calls:
                content = (message.content or "").strip()
                try:
                    final_result = json.loads(content)
                except json.JSONDecodeError as exc:
                    task_state.status = "failed"
                    task_state.errors.append(f"invalid_json:{exc}")
                    current.update({
                        "task_state": task_state,
                        "status": "failed",
                        "error": f"invalid_json:{exc}",
                        "error_emitted": True,
                    })
                    emit(writer, current, "error", f"模型未调用结果工具且不是合法 JSON：{exc}")
                    return current
                messages.append({"role": "assistant", "content": content})
                current.update({"task_state": task_state, "messages": messages, "final_result": final_result})
                return current

            serialized_calls = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ]
            messages.append({"role": "assistant", "content": message.content or "", "tool_calls": serialized_calls})

            final_call = next((call for call in tool_calls if call.function.name == state["final_tool"]), None)
            if final_call is not None:
                try:
                    final_result = json.loads(final_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    final_result = {}
                current.update({
                    "task_state": task_state,
                    "messages": messages,
                    "pending_calls": [],
                    "final_result": final_result,
                })
                return current

            pending_calls = [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                }
                for call in tool_calls
            ]
            current.update({
                "task_state": task_state,
                "messages": messages,
                "pending_calls": pending_calls,
            })
            return current

        def route_after_agent(state: RuntimeGraphState) -> str:
            if state.get("status") == "failed":
                return "fail"
            if state.get("final_result") is not None:
                return "validate_final"
            if state.get("pending_calls"):
                return "tools"
            return "fail"

        async def execute_tools_node(state: RuntimeGraphState) -> RuntimeGraphState:
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            step = int(state.get("step") or 0)
            calls = list(state.get("pending_calls") or [])
            allowed_tools = set(state.get("allowed_tools") or original_tool_names)
            if task_state.tool_calls + len(calls) > settings.max_agent_tool_calls:
                task_state.status = "failed"
                task_state.errors.append("max_tool_calls_exceeded")
                current = {**state, "task_state": task_state, "status": "failed", "error": "max_tool_calls_exceeded", "error_emitted": True}
                emit(writer, current, "error", f"Agent 超过最大工具调用次数 {settings.max_agent_tool_calls}")
                return current

            specs: list[dict[str, Any]] = []
            for call in calls:
                name = str(call.get("name") or "")
                try:
                    arguments = json.loads(str(call.get("arguments") or "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                normalized, argument_errors = validate_arguments(name, arguments)
                plan_step = PlanStep(step=step, tool=name, arguments=arguments, status="running", attempts=1)
                task_state.plan.append(plan_step)
                task_state.tool_calls += 1
                current = {**state, "task_state": task_state, "step": step}
                emit(writer, current, "plan", plan_step.model_dump(mode="json"))
                if argument_errors or normalized is None or name not in allowed_tools:
                    if argument_errors or normalized is None:
                        result = {"ok": False, "error": "invalid_arguments", "validation": {"errors": argument_errors}}
                        validation = validate_tool_result(name, result, arguments)
                    else:
                        result = {"ok": False, "error": f"工具 {name} 不在当前任务白名单中"}
                        validation = validate_tool_result(name, result, normalized)
                    specs.append({
                        "call": call,
                        "name": name,
                        "arguments": arguments,
                        "normalized": normalized,
                        "result": result,
                        "validation": validation,
                        "duration_ms": 0,
                        "plan_step": plan_step,
                    })
                    continue
                emit(writer, current, "tool_start", {"tool": name, "arguments": normalized})
                specs.append({
                    "call": call,
                    "name": name,
                    "arguments": arguments,
                    "normalized": normalized,
                    "result": None,
                    "validation": None,
                    "duration_ms": 0,
                    "plan_step": plan_step,
                })

            async def execute_spec(spec: dict[str, Any]) -> dict[str, Any]:
                if spec["result"] is not None:
                    return spec
                started = time.perf_counter()
                result = await self._execute_tool(
                    tools,
                    spec["name"],
                    spec["normalized"],
                    trace_context=trace_context,
                )
                spec["result"] = result
                spec["validation"] = validate_tool_result(spec["name"], result, spec["normalized"])
                spec["duration_ms"] = int((time.perf_counter() - started) * 1000)
                return spec

            completed = await asyncio.gather(*(execute_spec(spec) for spec in specs))
            messages = list(state.get("messages") or [])
            tool_payloads = dict(state.get("tool_payloads") or {})
            tool_history = list(state.get("tool_history") or [])
            terminal_failures: list[list[str]] = []
            for spec in completed:
                name = spec["name"]
                result = spec["result"]
                validation = spec["validation"]
                duration_ms = int(spec["duration_ms"])
                plan_step = spec["plan_step"]
                plan_step.status = "succeeded" if validation.ok else "failed"
                plan_step.duration_ms = duration_ms
                plan_step.error = ";".join(validation.errors)
                result_meta = {
                    "tool": name,
                    "arguments": spec.get("normalized") or spec.get("arguments") or {},
                    "transport": tools.transport_for(name) if hasattr(tools, "transport_for") else getattr(tools, "transport_name", "local"),
                    "result": result,
                    "ok": validation.ok,
                    "validation": validation.model_dump(mode="json"),
                    "duration_ms": duration_ms,
                    "attempts": int((result or {}).get("attempts") or 1),
                }
                if not validation.ok:
                    result_meta["instructions"] = self._replan_guidance(task_state, validation.errors)
                if name == "query_flight" and isinstance(result_meta.get("result"), dict):
                    result_meta["result"] = {
                        **result_meta["result"],
                        "flights": [
                            {key: value for key, value in item.items() if key != "flight_number"}
                            for item in (result_meta["result"].get("flights") or [])
                        ],
                    }
                current = {**state, "task_state": task_state, "step": step, "messages": messages}
                if validation.ok:
                    if validation.warnings:
                        task_state.warnings.extend(validation.warnings)
                    task_state.add_evidence(validation.evidence)
                    if name == "query_train" and (result.get("no_service") or result.get("no_tickets")):
                        result_meta["planning_signal"] = "no_service" if result.get("no_service") else "no_tickets"
                        result_meta["instructions"] = "该铁路路段没有可购余票。不要调用高德寻找枢纽；请你基于地理常识提出 1-2 个合理的铁路换乘、航班中转或铁路接驳航班方案，然后只查询这些方案涉及的关键路段。"
                        emit(writer, current, "replan", {
                            "attempt": "candidate_plan_from_llm",
                            "reason": ["no_service"],
                            "tool": name,
                        })
                    emit(writer, current, "evidence", {"items": [item.model_dump(mode="json") for item in validation.evidence]})
                    emit(writer, current, "tool_result", result_meta, duration_ms=duration_ms)
                else:
                    error_text = str((result or {}).get("error") or "")
                    recoverable_rail_failure = name == "query_train" and any(
                        marker in error_text for marker in ("反爬", "访问受限", "查询失败")
                    )
                    if recoverable_rail_failure:
                        task_state.warnings.append("rail_provider_temporarily_unavailable")
                        result_meta["planning_signal"] = "provider_unavailable"
                        result_meta["instructions"] = "12306 暂时不可用。不要调用高德寻找枢纽；请你提出 1-2 个合理的铁路换乘、航班中转或铁路接驳航班方案，再验证关键路段，并在最终结果中说明铁路数据暂时缺失。"
                        emit(writer, current, "validation", {"stage": "tool", "tool": name, **validation.model_dump(mode="json")})
                        emit(writer, current, "replan", {
                            "attempt": "continue_without_rail",
                            "reason": ["rail_provider_unavailable"],
                            "tool": name,
                        })
                    else:
                        self._record_failure(task_state, validation.errors)
                        emit(writer, current, "validation", {"stage": "tool", "tool": name, **validation.model_dump(mode="json")})
                        if task_state.replan_count <= task_state.max_replans:
                            emit(writer, current, "replan", {"attempt": task_state.replan_count, "reason": validation.errors, "tool": name})
                        else:
                            task_state.status = "failed"
                            terminal_failures.append(validation.errors)
                    emit(writer, current, "tool_result", result_meta, duration_ms=duration_ms)
                tool_payloads[name] = result or {}
                tool_history.append({
                    "name": name,
                    "arguments": spec.get("normalized") or spec.get("arguments") or {},
                    "result": result or {},
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(spec["call"].get("id") or ""),
                    "content": json.dumps(
                        self._compact_tool_result(
                            name,
                            result or {},
                            spec.get("normalized") or spec.get("arguments") or {},
                            result_meta,
                        ),
                        ensure_ascii=False,
                        default=str,
                    ),
                })

            status = "failed" if terminal_failures else "running"
            error_emitted = bool(terminal_failures)
            current = {
                **state,
                "task_state": task_state,
                "messages": messages,
                "pending_calls": [],
                "tool_payloads": tool_payloads,
                "tool_history": tool_history,
                "status": status,
                "error": terminal_failures[-1] if terminal_failures else None,
                "error_emitted": error_emitted,
            }
            if terminal_failures:
                emit(writer, current, "error", {
                    "code": "tool_validation_failed",
                    "errors": terminal_failures[-1],
                    "replans_exhausted": True,
                })
            return current

        async def policy_gate_node(state: RuntimeGraphState) -> RuntimeGraphState:
            if state.get("task") != "transport_plan":
                return {}
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            allowed_tools = list(state.get("allowed_tools") or original_tool_names)
            constraints = state.get("constraints") or {}
            train_results = [
                item for item in (state.get("tool_history") or [])
                if item.get("name") == "query_train"
            ]
            itinerary = build_rail_itinerary(
                train_results,
                str(constraints.get("origin") or ""),
                str(constraints.get("destination") or ""),
            )
            rail_available = bool(itinerary.get("available"))
            rail_minutes = int(itinerary.get("total_minutes") or 0)
            direct_rail_available = bool(itinerary.get("direct"))
            query_train_attempts = len(train_results)
            # No direct rail must not stop exploration. Open flights as soon as
            # the direct query has been attempted so the LLM can compare
            # rail transfers, air transfers and rail-air combinations by value.
            if rail_available:
                flight_allowed = rail_minutes > 360 or not direct_rail_available
            else:
                flight_allowed = query_train_attempts >= 1
            before = list(allowed_tools)
            if flight_allowed and "query_flight" in original_tool_names and "query_flight" not in allowed_tools:
                allowed_tools.append("query_flight")
            if not flight_allowed and "query_flight" in allowed_tools:
                allowed_tools = [name for name in allowed_tools if name != "query_flight"]
            if allowed_tools != before:
                current = {**state, "task_state": task_state, "allowed_tools": allowed_tools}
                if flight_allowed and direct_rail_available:
                    message = f"交通策略门控：直达铁路约 {rail_minutes} 分钟，同步比较航班性价比"
                elif flight_allowed:
                    message = "交通策略门控：直达铁路不可用或耗时不合适，开放中转铁路、航班及多式联运比较"
                else:
                    message = f"交通策略门控：直达铁路约 {rail_minutes} 分钟且少于 6 小时，暂不调用航班"
                emit(writer, current, "status", message)
            return {"allowed_tools": allowed_tools, "task_state": task_state}

        def route_after_tools(state: RuntimeGraphState) -> str:
            return "fail" if state.get("status") == "failed" else "agent"

        async def validate_final_node(state: RuntimeGraphState) -> RuntimeGraphState:
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            final_result = dict(state.get("final_result") or {})
            validation = validate_final_result(state["final_tool"], final_result)
            current = {**state, "task_state": task_state}
            if validation.ok:
                if validation.warnings:
                    task_state.warnings.extend(validation.warnings)
                task_state.status = "degraded" if task_state.errors or task_state.warnings else "succeeded"
                return {**current, "task_state": task_state, "status": task_state.status}
            self._record_final_failure(task_state, validation.errors)
            emit(writer, current, "validation", {"stage": "final", "ok": False, "errors": validation.errors, "warnings": validation.warnings})
            if task_state.replan_count <= task_state.max_replans:
                messages = list(state.get("messages") or [])
                messages.append({"role": "user", "content": "最终结果未通过 Schema 校验，请修正后重新提交；不得编造缺失信息。"})
                emit(writer, current, "replan", {"attempt": task_state.replan_count, "reason": validation.errors})
                return {
                    **current,
                    "task_state": task_state,
                    "messages": messages,
                    "final_result": None,
                    "status": "running",
                }
            task_state.status = "failed"
            current.update({"task_state": task_state, "status": "failed", "error": validation.errors, "error_emitted": True})
            emit(writer, current, "error", {"code": "final_validation_failed", "errors": validation.errors, "replans_exhausted": True})
            return current

        def route_after_final(state: RuntimeGraphState) -> str:
            if state.get("status") == "failed":
                return "fail"
            if state.get("final_result") is not None:
                return "finalize"
            return "agent"

        async def finalize_node(state: RuntimeGraphState) -> RuntimeGraphState:
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            current = {**state, "task_state": task_state}
            emit(writer, current, "done", dict(state.get("final_result") or {}))
            return {**current, "status": task_state.status}

        async def fail_node(state: RuntimeGraphState) -> RuntimeGraphState:
            writer = get_stream_writer()
            task_state = state["task_state"].model_copy(deep=True)
            task_state.status = "failed"
            current = {**state, "task_state": task_state, "status": "failed"}
            if not state.get("error_emitted"):
                emit(writer, current, "error", {"code": "graph_failed", "detail": state.get("error") or "Agent 未产生结果"})
            return current

        builder = StateGraph(RuntimeGraphState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", execute_tools_node)
        builder.add_node("policy_gate", policy_gate_node)
        builder.add_node("validate_final", validate_final_node)
        builder.add_node("finalize", finalize_node)
        builder.add_node("fail", fail_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent, {
            "tools": "tools",
            "validate_final": "validate_final",
            "fail": "fail",
        })
        builder.add_edge("tools", "policy_gate")
        builder.add_conditional_edges("policy_gate", route_after_tools, {
            "agent": "agent",
            "fail": "fail",
        })
        builder.add_conditional_edges("validate_final", route_after_final, {
            "finalize": "finalize",
            "agent": "agent",
            "fail": "fail",
        })
        builder.add_edge("finalize", END)
        builder.add_edge("fail", END)
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    async def _execute_tool(
        tools: ToolGateway,
        name: str,
        arguments: dict[str, Any],
        *,
        trace_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with observation(
            name=f"tool:{name}",
            as_type="tool",
            input=arguments,
            metadata={"tool": name, "runtime": "langgraph"},
            trace_context=trace_context,
        ) as tool_span:
            result = await tools.execute(name, arguments)
            ok = bool(result.get("ok")) if isinstance(result, dict) else False
            update(tool_span, output=trace_value(result), metadata={"tool": name, "ok": ok})
            return result

    @staticmethod
    def _compact_tool_result(
        name: str,
        result: dict[str, Any],
        arguments: dict[str, Any],
        result_meta: dict[str, Any],
    ) -> dict[str, Any]:
        compact: dict[str, Any] = {
            "tool": name,
            "ok": bool(result.get("ok")),
            "arguments": arguments,
        }
        if result_meta.get("planning_signal"):
            compact["planning_signal"] = result_meta["planning_signal"]
        if result_meta.get("instructions"):
            compact["instructions"] = result_meta["instructions"]
        validation = result_meta.get("validation") or {}
        if validation.get("errors") or validation.get("warnings"):
            compact["validation"] = {
                "errors": list(validation.get("errors") or []),
                "warnings": list(validation.get("warnings") or []),
            }
        if result.get("error"):
            compact["error"] = result["error"]
        if name == "route_estimate":
            route = result.get("route") or {}
            compact["route"] = {
                key: route.get(key)
                for key in (
                    "origin_name", "destination_name", "origin_location", "destination_location",
                    "distance_m", "duration_s", "tolls",
                )
                if route.get(key) is not None
            }
        elif name == "query_train":
            compact["no_service"] = bool(result.get("no_service"))
            compact["no_tickets"] = bool(result.get("no_tickets"))
            compact["train_summary"] = result.get("train_summary") or {}
            compact["ticket_periods"] = [
                {
                    "label": item.get("label"),
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                    "min_price": item.get("min_price"),
                    "duration_minutes": item.get("duration_minutes"),
                }
                for item in (result.get("ticket_periods") or [])[:12]
            ]
        elif name == "query_flight":
            compact["flights"] = [
                {
                    "airline": item.get("airline"),
                    "price": item.get("price"),
                    "duration_minutes": item.get("duration_minutes"),
                    "departure_time": item.get("departure_time"),
                    "arrival_time": item.get("arrival_time"),
                    "departure_airport": item.get("departure_airport"),
                    "arrival_airport": item.get("arrival_airport"),
                    "stops": item.get("stops"),
                }
                for item in (result.get("flights") or [])[:6]
            ]
        elif name == "resolve_transport_hubs":
            compact["candidate_hubs"] = [
                {
                    "name": item.get("name"),
                    "city": item.get("city"),
                    "type": item.get("type"),
                    "distance_m": item.get("distance_m"),
                    "role": item.get("role"),
                }
                for item in (result.get("candidate_hubs") or [])[:6]
            ]
        elif name == "get_weather":
            weather = result.get("weather") or {}
            compact["weather"] = {
                "city": weather.get("city"),
                "report_time": weather.get("report_time"),
                "casts": (weather.get("casts") or [])[:5],
            }
        elif name == "get_attraction_detail":
            poi = result.get("poi") or {}
            compact["poi"] = {
                key: poi.get(key)
                for key in ("poi_id", "name", "city", "district", "address", "location", "rating", "cost", "open_time", "tel")
                if poi.get(key) is not None
            }
            compact["nearby"] = [
                {"name": item.get("name"), "type": item.get("type"), "location": item.get("location")}
                for item in (result.get("nearby") or [])[:5]
            ]
        else:
            compact["result"] = result
        text = json.dumps(compact, ensure_ascii=False, default=str)
        if len(text) > 12000:
            return {
                "tool": name,
                "ok": bool(result.get("ok")),
                "arguments": arguments,
                "summary": text[:12000],
                "truncated": True,
            }
        return compact

    @staticmethod
    def _usage_dict(usage: Any) -> dict[str, int]:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            raw = usage.model_dump()
        elif isinstance(usage, dict):
            raw = usage
        else:
            raw = {}
        return {key: int(value) for key, value in raw.items() if isinstance(value, int)}

    @staticmethod
    def _record_failure(state: TaskState, errors: list[str]) -> None:
        state.replan_count += 1
        state.errors.extend(errors)
        state.status = "running"

    @staticmethod
    def _record_final_failure(state: TaskState, errors: list[str]) -> None:
        state.replan_count += 1
        state.errors.extend(errors)

    @staticmethod
    def _replan_guidance(state: TaskState, errors: list[str]) -> str:
        if state.replan_count <= state.max_replans:
            return f"工具结果校验失败：{';'.join(errors)}。请重新规划，优先尝试其他有来源的工具；仍失败则明确降级并在最终结果中说明。"
        return "重规划次数已达到上限。禁止再调用失败工具或编造事实，必须基于已有证据立即提交最终结果，缺失字段保持为空并写入 caveats。"
