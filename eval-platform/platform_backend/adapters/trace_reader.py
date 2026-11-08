from __future__ import annotations

from typing import Any

from ..core.tracing import SpanRecord


def read_agent_run_trace(run_record: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert a persisted main-project Agent Run only when timing is present.

    The current Agent Run event log does not persist per-event start/duration.
    In that case, return an explicit unavailable span rather than manufacturing
    a timeline from event order.
    """

    if not run_record:
        return [
            SpanRecord(
                trace_id="unavailable",
                span_id="unavailable",
                parent_span_id=None,
                name="main_agent_trace",
                span_type="agent",
                status="unavailable",
                start_ms=0,
                duration_ms=0,
                error_text="agent_run_not_found",
            ).as_dict()
        ]
    events = run_record.get("events") or []
    timed = [
        event
        for event in events
        if event.get("start_ms") is not None and event.get("duration_ms") is not None
    ]
    if not timed:
        return [
            SpanRecord(
                trace_id=str(run_record.get("id") or "unavailable"),
                span_id="unavailable",
                parent_span_id=None,
                name="main_agent_trace",
                span_type="agent",
                status="unavailable",
                start_ms=0,
                duration_ms=0,
                output={"event_count": len(events)},
                error_text="source_events_missing_timing",
            ).as_dict()
        ]
    trace_id = str(run_record.get("id") or "agent-run")
    return [
        SpanRecord(
            trace_id=trace_id,
            span_id=str(event.get("span_id") or f"event-{index}"),
            parent_span_id=event.get("parent_span_id"),
            name=str(event.get("name") or event.get("event") or "event"),
            span_type=str(event.get("type") or "agent"),
            status=str(event.get("status") or "passed"),
            start_ms=float(event["start_ms"]),
            duration_ms=float(event["duration_ms"]),
            input=event.get("input"),
            output=event.get("data"),
            metadata=event.get("meta") or {},
            error_text=str(event.get("error_text") or ""),
            tokens_in=event.get("tokens_in"),
            tokens_out=event.get("tokens_out"),
            cost_usd=event.get("cost_usd"),
            model=event.get("model"),
        ).as_dict()
        for index, event in enumerate(timed)
    ]
