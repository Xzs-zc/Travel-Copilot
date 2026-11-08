from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_backend.adapters.trace_reader import read_agent_run_trace  # noqa: E402
from platform_backend.core.tracing import span, trace_session  # noqa: E402


@pytest.mark.asyncio
async def test_spans_use_real_time_and_parent_relationships() -> None:
    with trace_session("trace-test") as tracer:
        with span("root", "agent", input={"case": "trace-test"}) as root:
            with span("child", "task", input={"sleep_ms": 60}) as child:
                await asyncio.sleep(0.06)
                child.update(output={"ok": True})
            root.update(output={"ok": True})

    records = {item["name"]: item for item in tracer.records()}
    assert records["child"]["duration_ms"] >= 50
    assert records["root"]["duration_ms"] >= records["child"]["duration_ms"]
    assert records["child"]["parent_span_id"] == records["root"]["span_id"]
    assert records["child"]["start_ms"] >= records["root"]["start_ms"]
    assert records["child"]["output"] == {"ok": True}


def test_agent_trace_without_timing_is_explicitly_unavailable() -> None:
    spans = read_agent_run_trace(
        {
            "id": "agent-run-1",
            "events": [{"event": "tool_result", "step": 1}],
        }
    )
    assert spans[0]["status"] == "unavailable"
    assert spans[0]["error_text"] == "source_events_missing_timing"
