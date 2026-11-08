from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.app.evaluation.support_blind import BlindCase, SupportBlindRunner
from openai import AsyncOpenAI
from sqlalchemy import text

from ..core.tracing import record_span, span


class OnlineSupportExecutor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.runner = SupportBlindRunner(
            cases_path=Path("/tmp/online-support-e2e-case.jsonl"),
            output_root=Path("/tmp/online-support-e2e"),
            settings=self.settings,
        )
        self.ready = False

    async def setup(self) -> None:
        if self.ready:
            return
        self.runner.client = AsyncOpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            timeout=90,
            max_retries=0,
        )
        await self.runner._prepare_database()
        self.runner.cases = []
        self.service_rag = self.runner._new_rag(mode="hybrid")
        await self.service_rag.ensure_index()
        self.ready = True

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        await self.setup()
        blind_case = BlindCase.from_dict(
            {
                "id": input_data.get("case_key") or "online-case",
                "block": "e2e",
                "message": input_data.get("message") or "",
                "context": input_data.get("context") or {},
                "expected": input_data.get("expected") or {},
                "tags": ["online"],
                "difficulty": input_data.get("difficulty") or "medium",
            }
        )
        with span(
            "support.stream_reply",
            "agent",
            input={
                "message": blind_case.message,
                "context": blind_case.context,
            },
            metadata={
                "timing_scope": "consumer_arrival",
                "timeout_seconds": 90,
            },
        ) as stream_span:
            try:
                result = await asyncio.wait_for(
                    self.runner._run_live_case(
                        blind_case,
                        service_rag=self.service_rag,
                    ),
                    timeout=90,
                )
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                stream_span.update(
                    status="error",
                    error_text=error_text,
                    output={"status": "error"},
                )
                return {
                    "status": "error",
                    "duration_ms": 90000
                    if isinstance(exc, asyncio.TimeoutError)
                    else None,
                    "route": {},
                    "tool_calls": [],
                    "reply": "",
                    "error": error_text,
                    "token_usage": None,
                    "rag_usage": None,
                    "event_timings": [],
                    "timing_scope": "consumer_arrival",
                }

            event_timings = result.actual.get("event_timings") or []
            base_start = (
                stream_span.record.start_ms
                if stream_span.record is not None
                else 0.0
            )
            for index, timing in enumerate(event_timings):
                offset_ms = float(timing.get("offset_ms") or 0)
                next_offset = (
                    float(event_timings[index + 1].get("offset_ms") or 0)
                    if index + 1 < len(event_timings)
                    else float(result.duration_ms)
                )
                record_span(
                    f"support.event.{timing.get('event') or 'unknown'}",
                    "event",
                    start_ms=base_start + offset_ms,
                    duration_ms=max(0.0, next_offset - offset_ms),
                    output=timing.get("data"),
                    metadata={"timing_scope": "consumer_arrival"},
                )

            error = bool(result.actual.get("error")) or not bool(
                result.actual.get("done")
            )
            status = (
                "error" if error else "passed" if result.passed else "failed"
            )
            output = {
                "status": status,
                "duration_ms": result.duration_ms,
                "route": result.actual.get("route") or {},
                "tool_calls": result.actual.get("tools") or [],
                "reply": result.actual.get("reply") or "",
                "error": result.actual.get("error") or "",
                "token_usage": result.actual.get("token_usage"),
                "rag_usage": result.actual.get("rag_usage"),
                "event_timings": event_timings,
                "timing_scope": "consumer_arrival",
            }
            stream_span.update(
                output=output,
                status="error" if status == "error" else "passed",
                error_text=str(output["error"]) if output["error"] else "",
            )
            return output

    async def close(self) -> None:
        if not self.ready:
            return
        if self.runner.client is not None:
            await self.runner.client.close()
        if self.runner.engine is not None:
            await self.runner.engine.dispose()
        if self.runner.admin_engine is not None:
            async with self.runner.admin_engine.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{self.runner.schema}" CASCADE')
                )
            await self.runner.admin_engine.dispose()
        self.ready = False


_EXECUTOR: OnlineSupportExecutor | Any | None = None


async def run_support_case(
    input_data: dict[str, Any],
    *,
    executor: Any | None = None,
) -> dict[str, Any]:
    global _EXECUTOR
    if executor is None:
        if _EXECUTOR is None:
            _EXECUTOR = OnlineSupportExecutor()
        executor = _EXECUTOR
    return await executor.execute(input_data)


async def close_support_executor() -> None:
    global _EXECUTOR
    if _EXECUTOR is not None and hasattr(_EXECUTOR, "close"):
        await _EXECUTOR.close()
    _EXECUTOR = None
